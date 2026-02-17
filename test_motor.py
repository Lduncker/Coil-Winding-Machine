import argparse
import time
import serial


def tx(ser: serial.Serial, cmd: str, pause: float = 0.05):
    """Transmit one ASCII command terminated by <CR> and print the echo/response."""
    ser.write(cmd.encode() + b"\r")
    ser.flush()
    reply = ser.readline().decode(errors="ignore").strip()
    print(f"sent {cmd!r}  ↩ {reply!r}")
    time.sleep(pause)


def main():
    p = argparse.ArgumentParser(description="One-shot Parker OEM650X command sender")
    p.add_argument("-p", "--port", default="/dev/cu.usbserial-FT57KM630",
                   help="Serial device (see ls /dev/cu.*)")
    p.add_argument("-b", "--baud", type=int, default=9600, help="Baud rate")
    # p.add_argument("--mr", type=int, default=10000,
    #                help="MR value: relative move in micro-steps")
    # p.add_argument("--accel", type=float, default=5,
    #                help="Acceleration parameter A (rev/s²)")
    # p.add_argument("--velocity", type=float, default=5,
    #                help="Target speed V in rev/s")
    # p.add_argument("--distance", type=int, default=200000,
    #                help="Distance D (full revolutions for absolute move)")
    args = p.parse_args()


    print(f"Opening {args.port} @ {args.baud}-8-N-1 …")
    try:
        with serial.Serial(port=args.port,
                           baudrate=args.baud,
                           bytesize=serial.EIGHTBITS,
                           parity=serial.PARITY_NONE,
                           stopbits=serial.STOPBITS_ONE,
                           timeout=0.5) as ser:
            time.sleep(0.2)                  # settle USB adapter
            ser.reset_input_buffer()
            ser.reset_output_buffer()

            tx(ser, "MN")                    # Normal/enable
            tx(ser, "LD3")                   # Disable limits
            tx(ser, "MR1000")          # Relative move
            tx(ser, "A1")        # Acceleration
            tx(ser, "V1.5")        # Velocity
            tx(ser, "D20000" )
            tx(ser, "G" )





            print("Done — port closed.")


    except serial.SerialException as err:
        print(f"Serial error: {err}")


if __name__ == "__main__":
    main()
