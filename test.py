import argparse
import time
import serial


def main():
    parser = argparse.ArgumentParser(description="One-shot serial tester")
    parser.add_argument("-p", "--port",
                        default="/dev/cu.usbserial-FT57KM630",
                        help="Serial device (see ls /dev/cu.*)")
    parser.add_argument("-b", "--baud",
                        type=int,
                        default=9600,
                        help="Baud rate")
    parser.add_argument("--delay",
                        type=float,
                        default=0.05,
                        help="Pause (s) between bytes")
    args = parser.parse_args()

    print(f"Opening {args.port} @ {args.baud}-8-N-1 …")
    try:
        with serial.Serial(args.port, args.baud, timeout=0.5) as ser:
            time.sleep(0.2)
            ser.reset_input_buffer()
            ser.reset_output_buffer()

            for ch in ("A", "B", "C"):
                ser.write(ch.encode())
                ser.flush()
                print(f"Sent {ch!r}")

            print("Done — exiting.")

    except serial.SerialException as e:
        print(f"ERROR: {e}")


if __name__ == "__main__":
    main()
