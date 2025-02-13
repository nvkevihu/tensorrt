#!/bin/bash

nvidia-smi

set -x

BASE_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"

python ${BASE_DIR}/infer.py \
    --data_dir=/data/em_segmentation \
    --input_saved_model_dir=/models/nvidia_examples/unet_medical_tf2 \
    --batch_size=8 \
    --output_tensors_name="output_1" \
    --total_max_samples=6500 \
    ${@}