FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive \
    TZ=Etc/UTC

# --- base deps + Gazebo Garden apt repo ---
RUN apt-get update && apt-get install -y --no-install-recommends \
        sudo git wget curl lsb-release gnupg build-essential cmake \
        software-properties-common ca-certificates tzdata \
    && wget https://packages.osrfoundation.org/gazebo.gpg -O /usr/share/keyrings/pkgs-osrf-archive-keyring.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/pkgs-osrf-archive-keyring.gpg] http://packages.osrfoundation.org/gazebo/ubuntu-stable $(lsb_release -cs) main" \
        > /etc/apt/sources.list.d/gazebo-stable.list \
    && add-apt-repository -y ppa:deadsnakes/ppa \
    && apt-get update && apt-get install -y --no-install-recommends \
        gz-harmonic rapidjson-dev libopencv-dev \
        libgstreamer1.0-dev libgstreamer-plugins-base1.0-dev \
        libgl1-mesa-dri libglx-mesa0 mesa-utils x11-apps \
        python3.9 python3.9-venv python3.9-dev \
    && rm -rf /var/lib/apt/lists/*

# ArduPilot's build scripts refuse to run as root
RUN useradd -m -s /bin/bash ardupilot \
    && echo "ardupilot ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/ardupilot
USER ardupilot
ENV USER=ardupilot
WORKDIR /home/ardupilot

# --- ArduPilot SITL ---
RUN git clone --recurse-submodules -j8 https://github.com/ArduPilot/ardupilot.git
RUN cd ardupilot && Tools/environment_install/install-prereqs-ubuntu.sh -y
ENV PATH="/home/ardupilot/.local/bin:/home/ardupilot/ardupilot/Tools/autotest:${PATH}"
RUN cd ardupilot && ./waf configure --board sitl && ./waf copter

# --- ardupilot_gazebo plugin (Gazebo Garden) ---
RUN git clone https://github.com/ArduPilot/ardupilot_gazebo.git \
    && cd ardupilot_gazebo && mkdir build && cd build \
    && cmake .. -DCMAKE_BUILD_TYPE=RelWithDebInfo && make -j$(nproc)
ENV GZ_SIM_SYSTEM_PLUGIN_PATH=/home/ardupilot/ardupilot_gazebo/build:${GZ_SIM_SYSTEM_PLUGIN_PATH} \
    GZ_SIM_RESOURCE_PATH=/home/ardupilot/ardupilot_gazebo/models:/home/ardupilot/ardupilot_gazebo/worlds:${GZ_SIM_RESOURCE_PATH}

# --- python 3.9 venv for MAVProxy + dronekit (dronekit is broken on py>=3.10) ---
RUN python3.9 -m venv /home/ardupilot/venv \
    && /home/ardupilot/venv/bin/pip install --no-cache-dir -U pip wheel \
    && /home/ardupilot/venv/bin/pip install --no-cache-dir pymavlink dronekit dronekit-sitl pexpect future empy==3.3.4
ENV PATH="/home/ardupilot/venv/bin:${PATH}"

# tkinter for the GUI examples (own late layer so the slow ArduPilot layers above stay cached)
RUN sudo apt-get update && sudo apt-get install -y --no-install-recommends python3.9-tk \
    && sudo rm -rf /var/lib/apt/lists/*

RUN /home/ardupilot/venv/bin/pip install --no-cache-dir tkintermapview

COPY --chown=ardupilot:ardupilot entrypoint.sh start_sitl.sh /home/ardupilot/
RUN sed -i 's/\r$//' /home/ardupilot/entrypoint.sh /home/ardupilot/start_sitl.sh \
    && chmod +x /home/ardupilot/entrypoint.sh /home/ardupilot/start_sitl.sh \
    && mkdir /home/ardupilot/dronekit_examples

WORKDIR /home/ardupilot/dronekit_examples
ENTRYPOINT ["/home/ardupilot/entrypoint.sh"]
CMD ["bash"]