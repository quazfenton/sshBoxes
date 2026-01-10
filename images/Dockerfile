FROM ubuntu:22.04
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y openssh-server sudo python3 python3-pip && mkdir /var/run/sshd
# add user boxuser with no password
RUN useradd -m -s /bin/bash boxuser && echo "boxuser ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/boxuser
# allow ssh login
RUN sed -i 's/#PermitRootLogin prohibit-password/PermitRootLogin no/' /etc/ssh/sshd_config
# install asciinema for session recording
RUN pip3 install asciinema
EXPOSE 22
CMD ["/usr/sbin/sshd","-D"]