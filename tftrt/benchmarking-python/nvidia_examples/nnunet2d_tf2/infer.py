# Copyright (c) 2021, NVIDIA CORPORATION. All rights reserved.
#
# Copyright 2021 The TensorFlow Authors. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# =============================================================================

import os
import sys
from glob import glob

import numpy as np

import nvidia.dali.fn as fn
import nvidia.dali.ops as ops
import nvidia.dali.plugin.tf as dali_tf
from nvidia.dali.pipeline import Pipeline

import numpy as np

import tensorflow as tf

# Allow import of top level python files
import inspect

currentdir = os.path.dirname(
    os.path.abspath(inspect.getfile(inspect.currentframe()))
)
parentdir = os.path.dirname(currentdir)
basedir = os.path.dirname(parentdir)
sys.path.insert(0, basedir)

from benchmark_args import BaseCommandLineAPI
from benchmark_runner import BaseBenchmarkRunner
from benchmark_utils import patch_dali_dataset


class CommandLineAPI(BaseCommandLineAPI):

    def __init__(self):
        super(CommandLineAPI, self).__init__()

        self._parser.add_argument(
            "--num_image_slices",
            type=int,
            default=None,
            required=True,
            help="Number of 2D slices taken from the 3D "
            "imagery"
        )


# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%% #
# %%%%%%%%%%%%%%%%% IMPLEMENT MODEL-SPECIFIC FUNCTIONS HERE %%%%%%%%%%%%%%%%%% #
# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%% #


class BenchmarkRunner(BaseBenchmarkRunner):

    def get_dataset_batches(self):
        """Returns a list of batches of input samples.

        Each batch should be in the form [x, y], where
        x is a numpy array of the input samples for the batch, and
        y is a numpy array of the expected model outputs for the batch

        Returns:
        - dataset: a TF Dataset object
        - bypass_data_to_eval: any object type that will be passed unmodified to
                            `evaluate_result()`. If not necessary: `None`

        Note: script arguments can be accessed using `self._args.attr`
        """

        queue_size = 64
        num_threads = 32

        def get_files(data_dir, pattern):
            data = sorted(glob(os.path.join(data_dir, pattern)))

            assert data, f"No data found in {os.path.join(data_dir, pattern)}"

            for file in data:
                assert os.path.isfile(file), f"Not found: `{file}`"

            return data

        def get_reader(files):
            return ops.readers.Numpy(
                seed=0,
                files=files,
                device="cpu",
                read_ahead=True,
                shard_id=0,
                pad_last_batch=True,
                num_shards=1,
                dont_use_mmap=True,
                shuffle_after_epoch=False,
                prefetch_queue_depth=queue_size
            )

        def crop(x, num_image_slices):
            return fn.crop(
                x, crop=[num_image_slices, 192, 160], out_of_bounds_policy="pad"
            )

        class EvalPipeline(Pipeline):

            def __init__(self, imgs, lbls, batch_size, num_image_slices):
                super(EvalPipeline, self).__init__(
                    batch_size=batch_size,
                    num_threads=num_threads,
                    device_id=0,
                    prefetch_queue_depth=queue_size,
                    seed=0
                )
                self.input_x = get_reader(imgs)
                self.input_y = get_reader(lbls)

                self._num_image_slices = num_image_slices

            def define_graph(self):
                img, lbl = (
                    self.input_x(name="ReaderX").gpu(),
                    self.input_y(name="ReaderY").gpu()
                )
                img, lbl = (
                    fn.reshape(img, layout="DHWC"), fn.reshape(lbl, layout="DHWC")
                )
                img, lbl = (
                    crop(img, self._num_image_slices),
                    crop(lbl, self._num_image_slices)
                )
                return img, lbl

        x_files = get_files(self._args.data_dir, "*_x.npy")
        y_files = get_files(self._args.data_dir, "*_y.npy")

        pipeline = EvalPipeline(
            x_files,
            y_files,
            batch_size=self._args.batch_size,
            num_image_slices=self._args.num_image_slices
        )

        dataset = dali_tf.DALIDataset(
            pipeline,
            batch_size=self._args.batch_size,
            device_id=0,
            output_dtypes=(tf.float32, tf.uint8)
        )

        dataset = patch_dali_dataset(dataset)

        SAMPLES_IN_DATASET = 484
        dataset = dataset.take(int(SAMPLES_IN_DATASET / self._args.batch_size))

        return dataset, None

    def preprocess_model_inputs(self, data_batch):
        """This function prepare the `data_batch` generated from the dataset.
        Returns:
            x: input of the model
            y: data to be used for model evaluation

        Note: script arguments can be accessed using `self._args.attr`
        """
        x, y = data_batch

        if x.shape[0] != 1:
            # We merge together different batches into axis = 1
            # Example:
            # X: (2, 32, 192, 160, 4) => (64, 192, 160, 4)
            # Y: (2, 32, 192, 160, 1) => (64, 192, 160, 1)
            x = tf.concat(tf.unstack(x, x.shape[0], 0), axis=0)
            y = tf.concat(tf.unstack(y, y.shape[0], 0), axis=0)
        else:
            x, y = tf.squeeze(x, 0), tf.squeeze(y, 0)

        return x, y

    def postprocess_model_outputs(self, predictions, expected):
        """Post process if needed the predictions and expected tensors. At the
        minimum, this function transforms all TF Tensors into a numpy arrays.
        Most models will not need to modify this function.

        Note: script arguments can be accessed using `self._args.attr`
        """

        predictions = np.argmax(predictions.numpy(), axis=-1)
        expected = np.squeeze(expected.numpy(), axis=-1)

        if self._args.batch_size != 1:
            pred_shape = predictions.shape
            exp_shape = expected.shape
            split_ratio = int(pred_shape[0] / self._args.batch_size)
            predictions = np.reshape(
                predictions,
                (self._args.batch_size, split_ratio) + pred_shape[1:]
            )
            expected = np.reshape(
                expected, (self._args.batch_size, split_ratio) + exp_shape[1:]
            )

        return predictions, expected


    def evaluate_model(self, predictions, expected, bypass_data_to_eval):
        """Evaluate result predictions for entire dataset.

        This computes overall accuracy, mAP,  etc.  Returns the
        metric value and a metric_units string naming the metric.

        Note: script arguments can be accessed using `self._args.attr`
        """

        def get_stats(preds, target, class_idx):
            tp = np.logical_and(preds == class_idx, target == class_idx).sum()
            fn = np.logical_and(preds != class_idx, target == class_idx).sum()
            fp = np.logical_and(preds == class_idx, target != class_idx).sum()
            return tp, fn, fp

        dice = []

        preds_shape = predictions["data"].shape
        predictions = np.reshape(
            predictions["data"], [484, -1] + list(preds_shape[1:])
        )

        expected_shape = expected["data"].shape
        expected = np.reshape(
            expected["data"], [484, -1] + list(expected_shape[1:])
        )

        for y_pred, y_true in zip(predictions, expected):
            class_dice = []
            for i in range(1, 5):
                tp, fn, fp = get_stats(y_pred, y_true, i)
                score = 1 if tp == 0 else 2 * tp / (2*tp + fn + fp)
                class_dice.append(score)
            dice.append(np.mean(class_dice))

        return np.mean(dice) * 100, "Dice score"


if __name__ == '__main__':

    cmdline_api = CommandLineAPI()
    args = cmdline_api.parse_args()

    runner = BenchmarkRunner(args)

    runner.execute_benchmark()
