#!/bin/bash

IMAGE_NAME="yacron"
TAG="abc"

# Stop container.
docker stop $(docker ps -a | grep $IMAGE_NAME:$TAG |  awk '{print $1}')

# Delete container.
docker rm $(docker ps -a | grep $IMAGE_NAME:$TAG |  awk '{print $1}')

## Delete image.
#docker rmi --force $(docker images | grep $IMAGE_NAME | grep $TAG |  awk '{print $3}')
