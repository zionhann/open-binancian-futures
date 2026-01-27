TAG=${1:-latest}

scp -i ~/.ssh/aws25.pem src/main/.env ec2-user@zionhann.com:~/joshua/

docker buildx build --platform linux/arm64,linux/amd64 --no-cache -t zionhann/joshua:$TAG --push .