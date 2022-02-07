#!/bin/bash

IMAGE_NAME="yacron"
TAG="abc"

docker run -it --rm \
    --name yacron $IMAGE_NAME:$TAG

