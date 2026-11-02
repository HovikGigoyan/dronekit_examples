#!/bin/bash
set -e

# usage: start_sitl.sh [--no-gui]
#   (default)  Gazebo + ArduPilot SITL. With a working $DISPLAY (X server on the host)
#              Gazebo's GUI client and MAVProxy's map/console windows open too;
#              without it everything runs headless.
#   --no-gui   ArduPilot SITL only: no Gazebo, no windows. Uses SITL's built-in
#              physics, so it boots faster and needs far less CPU.
NO_GUI=0
for arg in "$@"; do
    case "$arg" in
        --no-gui) NO_GUI=1 ;;
        -h|--help) sed -n '4,9p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "Unknown option: $arg (try --help)" >&2; exit 1 ;;
    esac
done

ARDUCOPTER_DIR=/home/ardupilot/ardupilot/ArduCopter
OUT_PORTS="--out udp:127.0.0.1:14550 --out udp:127.0.0.1:14450"

# ArduPilot SITL + its own MAVProxy, extra output on :14450 for the dronekit examples.
# Runs in the foreground - Ctrl-C stops the whole stack. Run this in one terminal,
# then `docker exec -it <container> bash` in a second terminal to run the examples.
cd "$ARDUCOPTER_DIR"

if [ "$NO_GUI" -eq 1 ]; then
    sim_vehicle.py -v ArduCopter -f quad $OUT_PORTS \
        --no-rebuild -w --mavproxy-args="--daemon"
    exit 0
fi

if [ -n "$DISPLAY" ]; then
    GZ_FLAGS="-r -v1"
    MAVPROXY_EXTRA="--map --console"
else
    GZ_FLAGS="-s -r -v1"
    MAVPROXY_EXTRA=""
fi

gz sim $GZ_FLAGS iris_runway.sdf &
GZ_PID=$!
trap 'kill $GZ_PID 2>/dev/null' EXIT

sim_vehicle.py -v ArduCopter -f gazebo-iris --model JSON \
    --add-param-file=/home/ardupilot/ardupilot/Tools/autotest/default_params/gazebo-iris.parm \
    $OUT_PORTS \
    --no-rebuild -w --mavproxy-args="$MAVPROXY_EXTRA"
