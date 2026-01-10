FROM ubuntu:22.04

# Install packages
RUN apt-get update && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y openssh-server sudo python3 python3-pip && \
    mkdir /var/run/sshd

# Create user with limited privileges
RUN useradd -m -s /bin/bash -U -G sudo boxuser && \
    echo "boxuser ALL=(ALL) NOPASSWD:SETENV: /usr/bin/*" > /etc/sudoers.d/boxuser && \
    chmod 440 /etc/sudoers.d/boxuser

# Security hardening for SSH
RUN sed -i 's/#PermitRootLogin prohibit-password/PermitRootLogin no/' /etc/ssh/sshd_config && \
    sed -i 's/#PasswordAuthentication yes/PasswordAuthentication no/' /etc/ssh/sshd_config && \
    sed -i 's/#PubkeyAuthentication yes/PubkeyAuthentication yes/' /etc/ssh/sshd_config && \
    sed -i 's/#UsePAM yes/UsePAM no/' /etc/ssh/sshd_config && \
    sed -i 's/#AllowTcpForwarding yes/AllowTcpForwarding yes/' /etc/ssh/sshd_config && \
    sed -i 's/#X11Forwarding no/X11Forwarding no/' /etc/ssh/sshd_config && \
    sed -i 's/#ClientAliveInterval 0/ClientAliveInterval 300/' /etc/ssh/sshd_config && \
    sed -i 's/#ClientAliveCountMax 3/ClientAliveCountMax 2/' /etc/ssh/sshd_config

# install asciinema for session recording
RUN pip3 install asciinema

EXPOSE 22

# SSH daemon needs to run as root to bind to port 22
CMD ["/usr/sbin/sshd", "-D"]