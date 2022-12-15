import cv2
import logging
import mediapipe as mp
import numpy as np
from tqdm import tqdm
from typing import Tuple, Union
from xrprimer.utils.ffmpeg_utils import video_to_array
from xrprimer.utils.log_utils import get_logger

from xrmocap.transform.convention.keypoints_convention import get_keypoint_num
from .mmpose_top_down_estimator import MMposeTopDownEstimator


class MediapipeEstimator(MMposeTopDownEstimator):

    def __init__(self,
                 mediapipe_kwargs: dict,
                 bbox_thr: float = 0.0,
                 logger: Union[None, str, logging.Logger] = None) -> None:
        """Init a detector from mediapipe.

        Args:
            mediapipe_kwargs (dict):
                A dict contains args of mediapipe.
                refer to https://google.github.io/mediapipe/solutions/pose.html
                in detail.
            bbox_thr (float, optional):
                Threshold of a bbox. Those have lower scores will be ignored.
                Defaults to 0.0.
            logger (Union[None, str, logging.Logger], optional):
                Logger for logging. If None, root logger will be selected.
                Defaults to None.
        """
        # build the pose model
        mp_pose = mp.solutions.pose
        self.pose_model = mp_pose.Pose(**mediapipe_kwargs)
        self.bbox_thr = bbox_thr
        self.logger = get_logger(logger)
        self.convention = 'mediapipe_body'

    def get_keypoints_convention_name(self) -> str:
        """Get data_source from dataset type of the pose model.

        Returns:
            str:
                Name of the keypoints convention. Must be
                a key of KEYPOINTS_FACTORY.
        """
        return self.convention

    def infer_single_img(self, img_arr: np.ndarray, bbox_list: list):
        multi_kps2d = []
        for bbox_dict in bbox_list:
            bbox = bbox_dict['bbox']
            kps2d = None
            if bbox[4] > self.bbox_thr:
                img = img_arr[int(bbox[1]):int(bbox[3]),
                              int(bbox[0]):int(bbox[2])]
                result_mp = self.pose_model.process(
                    cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
                if result_mp.pose_landmarks:
                    kps_list = [[
                        landmark.x * img.shape[1] + bbox[0],
                        landmark.y * img.shape[0] + bbox[1],
                        landmark.visibility
                    ] for landmark in result_mp.pose_landmarks.landmark]
                    kps2d = np.array(kps_list)
            if kps2d is not None:
                multi_kps2d.append(dict(bbox=bbox, keypoints=kps2d))
        return multi_kps2d

    def infer_array(self,
                    image_array: Union[np.ndarray, list],
                    bbox_list: Union[tuple, list],
                    disable_tqdm: bool = False,
                    return_heatmap: bool = False) -> Tuple[list, list]:
        """Infer frames already in memory(ndarray type).

        Args:
            image_array (Union[np.ndarray, list]):
                BGR image ndarray in shape [n_frame, height, width, 3],
                or a list of image ndarrays in shape [height, width, 3] while
                len(list) == n_frame.
            bbox_list (Union[tuple, list]):
                A list of human bboxes.
                Shape of the nested lists is (n_frame, n_human, 5).
                Each bbox is a bbox_xyxy with a bbox_score at last.
            disable_tqdm (bool, optional):
                Whether to disable the entire progressbar wrapper.
                Defaults to False.
            return_heatmap (bool, optional):
                Whether to return heatmap.
                Defaults to False.

        Returns:
            Tuple[list, list]:
                keypoints_list (list):
                    A list of human keypoints.
                    Shape of the nested lists is
                    (n_frame, n_human, n_keypoints, 3).
                    Each keypoint is an array of (x, y, confidence).
                bbox_list (list):
                    A list of human bboxes.
                    Shape of the nested lists is (n_frame, n_human, 5).
                    Each bbox is a bbox_xyxy with a bbox_score at last.
                    It could be smaller than the input bbox_list,
                    if there's no keypoints detected in some bbox.
        """
        ret_kps_list = []
        ret_bbox_list = []
        n_frame = len(image_array)
        n_kps = get_keypoint_num(self.get_keypoints_convention_name())
        for frame_index in tqdm(range(0, n_frame), disable=disable_tqdm):
            img_arr = image_array[frame_index]
            bboxes_in_frame = []
            for idx, bbox in enumerate(bbox_list[frame_index]):
                if bbox[4] > 0.0:
                    bboxes_in_frame.append({'bbox': bbox, 'id': idx})
            if len(bboxes_in_frame) > 0:
                pose_results = self.infer_single_img(img_arr, bboxes_in_frame)
                frame_kps_results = np.zeros(
                    shape=(
                        len(bbox_list[frame_index]),
                        n_kps,
                        3,
                    ))
                frame_bbox_results = np.zeros(
                    shape=(len(bbox_list[frame_index]), 5))
                for idx, person_dict in enumerate(pose_results):
                    bbox = person_dict['bbox']
                    keypoints = person_dict['keypoints']
                    frame_bbox_results[idx] = bbox
                    frame_kps_results[idx] = keypoints
                frame_kps_results = frame_kps_results.tolist()
                frame_bbox_results = frame_bbox_results.tolist()
            else:
                frame_kps_results = []
                frame_bbox_results = []
            ret_kps_list += [frame_kps_results]
            ret_bbox_list += [frame_bbox_results]
        return ret_kps_list, None, ret_bbox_list

    def infer_frames(
            self,
            frame_path_list: list,
            bbox_list: Union[tuple, list],
            disable_tqdm: bool = False,
            return_heatmap: bool = False,
            load_batch_size: Union[None, int] = None) -> Tuple[list, list]:
        """Infer frames from file.

        Args:
            frame_path_list (list):
                A list of frames' absolute paths.
            bbox_list (Union[tuple, list]):
                A list of human bboxes.
                Shape of the nested lists is (n_frame, n_human, 5).
                Each bbox is a bbox_xyxy with a bbox_score at last.
            disable_tqdm (bool, optional):
                Whether to disable the entire progressbar wrapper.
                Defaults to False.
            return_heatmap (bool, optional):
                Whether to return heatmap.
                Defaults to False.
            load_batch_size (Union[None, int], optional):
                How many frames are loaded at the same time.
                Defaults to None, load all frames in frame_path_list.

        Returns:
            Tuple[list, list]:
                keypoints_list (list):
                    A list of human keypoints.
                    Shape of the nested lists is
                    (n_frame, n_human, n_keypoints, 3).
                    Each keypoint is an array of (x, y, confidence).
                bbox_list (list):
                    A list of human bboxes.
                    Shape of the nested lists is (n_frame, n_human, 5).
                    Each bbox is a bbox_xyxy with a bbox_score at last.
                    It could be smaller than the input bbox_list,
                    if there's no keypoints detected in some bbox.
        """
        ret_kps_list = []
        ret_boox_list = []
        if load_batch_size is None:
            load_batch_size = len(frame_path_list)
        for start_idx in range(0, len(frame_path_list), load_batch_size):
            end_idx = min(len(frame_path_list), start_idx + load_batch_size)
            if load_batch_size < len(frame_path_list):
                self.logger.info(
                    'Processing mediapipe on frames' +
                    f'({start_idx}-{end_idx})/{len(frame_path_list)}')
            image_array_list = []
            for frame_abs_path in frame_path_list[start_idx:end_idx]:
                img_np = cv2.imread(frame_abs_path)
                image_array_list.append(img_np)
            batch_pose_list, _, batch_boox_list = \
                self.infer_array(
                    image_array=image_array_list,
                    bbox_list=bbox_list[start_idx:end_idx],
                    disable_tqdm=disable_tqdm)
            ret_kps_list += batch_pose_list
            ret_boox_list += batch_boox_list
        return ret_kps_list, None, ret_boox_list

    def infer_video(self,
                    video_path: str,
                    bbox_list: Union[tuple, list],
                    disable_tqdm: bool = False,
                    return_heatmap: bool = False) -> Tuple[list, list]:
        """Infer frames from a video file.

        Args:
            video_path (str):
                Path to the video to be detected.
            bbox_list (Union[tuple, list]):
                A list of human bboxes.
                Shape of the nested lists is (n_frame, n_human, 5).
                Each bbox is a bbox_xyxy with a bbox_score at last.
            disable_tqdm (bool, optional):
                Whether to disable the entire progressbar wrapper.
                Defaults to False.
            return_heatmap (bool, optional):
                Whether to return heatmap.
                Defaults to False.

        Returns:
            Tuple[list, list]:
                keypoints_list (list):
                    A list of human keypoints.
                    Shape of the nested lists is
                    (n_frame, n_human, n_keypoints, 3).
                    Each keypoint is an array of (x, y, confidence).
                bbox_list (list):
                    A list of human bboxes.
                    Shape of the nested lists is (n_frame, n_human, 5).
                    Each bbox is a bbox_xyxy with a bbox_score at last.
                    It could be smaller than the input bbox_list,
                    if there's no keypoints detected in some bbox.
        """
        image_array = video_to_array(input_path=video_path, logger=self.logger)
        ret_kps_list, _, ret_boox_list = self.infer_array(
            image_array=image_array,
            bbox_list=bbox_list,
            disable_tqdm=disable_tqdm)
        return ret_kps_list, None, ret_boox_list
