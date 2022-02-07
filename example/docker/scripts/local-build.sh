#!/bin/bash

IMAGE_NAME="yacron"
TAG="abc"

# Docker build locally
cd ../docker/ubuntu && \
docker build --progress=plain --no-cache . -t $IMAGE_NAME:$TAG
