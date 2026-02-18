#!/usr/bin/env python3
"""
Enhanced Fiber‑winder GUI - Manual Mode

Features:
- Manual control via arrow keys (jogging)
- Parameter persistence (auto-save/load)
- Manual distance setting for fiber passes
"""

import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox
import threading, sys, serial, time, json, os
from enum import Enum
from dataclasses import dataclass
from typing import Optional

# ─── Motor & Parameter Definitions ────────────────────────────────────────────
class MotorNode(Enum):
    CORKSCREW    = 8  # axial feed
    LEFT_FIBER   = 4  # left fiber winder
    RIGHT_FIBER  = 6  # right fiber winder

@dataclass
class MotorParams:
    velocity: float = 2.0     # rev/s
    distance: int   = 2500    # micro‑steps
    accel:    float = 0.3     # rev/s²
    resolution: int = 1000    # MR parameter (steps/rev)

CONFIG_FILE = "winder_config.json"

# ─── Redirect print() into the GUI ────────────────────────────────────────────
class TextRedirector:
    def __init__(self, widget: tk.Text):
        self.text = widget

    def write(self, msg: str):
        self.text.after(0, lambda: (
            self.text.insert(tk.END, msg), self.text.see(tk.END)))

    def flush(self):
        pass

# ─── Enhanced Motor Controller with Limit Detection ──────────────────────────
class ParkerMotorController:
    def __init__(self, port: str, baud: int, timeout: float = 0.5):
        self.port = port
        self.baud = baud
        self.timeout = timeout
        self.ser: Optional[serial.Serial] = None
        self.is_connected = False
        self._lock = threading.Lock()

    def connect(self) -> bool:
        try:
            self.ser = serial.Serial(
                port=self.port,
                baudrate=self.baud,
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                timeout=self.timeout,
            )
            time.sleep(0.2)
            self.ser.reset_input_buffer()
            self.ser.reset_output_buffer()
            self.is_connected = self.ser.is_open

            # Initialize all three nodes
            for node in MotorNode:
                for cmd in (f"{node.value}MN", f"{node.value}LD3"):
                    self._tx(cmd)
            return True
        except Exception as exc:
            print(f"Connect error: {exc}")
            return False

    def disconnect(self):
        if self.ser and self.ser.is_open:
            self.ser.close()
        self.is_connected = False

    def _tx(self, pkt: str, pause: float = 0.02) -> str:
        with self._lock:
            if not self.is_connected:
                raise RuntimeError("Serial port not open")
            self.ser.write((pkt + "\r").encode())
            self.ser.flush()
            reply = self.ser.readline().decode(errors="ignore").strip()
            print(f"sent {pkt!r}  ↩ {reply!r}")
            time.sleep(pause)
            return reply

    def _get_position(self, node: MotorNode) -> int:
        """Get current position of a motor"""
        try:
            reply = self._tx(f"{node.value}RP")
            # Parse position from reply (format may vary)
            # Assuming reply format like "*8RP12345"
            position = int(reply.split('RP')[1]) if 'RP' in reply else 0
            return position
        except:
            return 0


    def _queue_move(self, node: MotorNode, params: MotorParams):
        # Set Normal Mode (preset moves) explicitly since Jog uses Continuous Mode
        self._tx(f"{node.value}MN")

        # Set resolution first
        self._tx(f"{node.value}MR{params.resolution}")

        # Velocity and acceleration are always positive
        vel = abs(float(params.velocity))
        acc = abs(params.accel)

        # Handle distance direction explicitly
        if params.distance < 0:
            dist_cmd = f"D-{abs(params.distance)}"
            print(f"Motor {node.name}: Running BACKWARD, distance = {params.distance}")
        else:
            dist_cmd = f"D{params.distance}"
            print(f"Motor {node.name}: Running FORWARD, distance = {params.distance}")

        # Send commands
        self._tx(f"{node.value}A{acc}")
        self._tx(f"{node.value}V{vel}")
        self._tx(f"{node.value}{dist_cmd}")

    def _go(self, node: MotorNode):
        self._tx(f"{node.value}G")

    def run_left_fiber_pass(self, corkscrew_params: MotorParams, left_fiber_params: MotorParams):
        """Run one fiber pass using left fiber winder + corkscrew"""
        try:
            print("Starting LEFT FIBER PASS (Corkscrew + Left Fiber)")

            self._queue_move(MotorNode.CORKSCREW, corkscrew_params)
            self._queue_move(MotorNode.LEFT_FIBER, left_fiber_params)

            self._go(MotorNode.CORKSCREW)
            self._go(MotorNode.LEFT_FIBER)

            print("Left fiber pass started")
        except Exception as exc:
            print(f"Left fiber pass error: {exc}")

    def run_right_fiber_pass(self, corkscrew_params: MotorParams, right_fiber_params: MotorParams):
        """Run one fiber pass using right fiber winder + corkscrew"""
        try:
            print("Starting RIGHT FIBER PASS (Corkscrew + Right Fiber)")

            self._queue_move(MotorNode.CORKSCREW, corkscrew_params)
            self._queue_move(MotorNode.RIGHT_FIBER, right_fiber_params)

            self._go(MotorNode.CORKSCREW)
            self._go(MotorNode.RIGHT_FIBER)

            print("Right fiber pass started")
        except Exception as exc:
            print(f"Right fiber pass error: {exc}")

    def estop(self):
        try:
            for node in MotorNode:
                self._tx(f"{node.value}Z", pause=0)
            print(">>> EMERGENCY STOP (Z) SENT TO ALL NODES <<<")
        except Exception as exc:
            print(f"E‑stop error: {exc}")

    def jog(self, node: MotorNode, direction: int, velocity: float):
        """
        Jog a motor in a direction (-1 or 1).
        Uses MC (Mode Continuous).
        """
        try:
            # Set Continuous Mode
            self._tx(f"{node.value}MC")
            # Set Accel/Vel (use a default reasonable accel for jogging)
            self._tx(f"{node.value}A1")
            self._tx(f"{node.value}V{abs(velocity)}")
            # Command direction
            if direction > 0:
                self._tx(f"{node.value}+")
            else:
                self._tx(f"{node.value}-")
            print(f"Jogging {node.name} direction {direction}")
        except Exception as e:
            print(f"Jog error: {e}")

    def stop_motor(self, node: MotorNode):
        try:
            self._tx(f"{node.value}S") # Stop
            print(f"Stopping {node.name}")
        except Exception as e:
            print(f"Stop error: {e}")

# ─── Enhanced GUI with Homing Features ───────────────────────────────────────
class WinderGUI(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Enhanced Copper/Fiber Winder Control")
        self.geometry("900x750")

        self.ctrl = ParkerMotorController("/dev/cu.usbserial-FT57KM630", 9600)
        self.is_running = False
        self.jogging_direction = 0  # 0=stopped, -1=left, 1=right

        # Motor parameters
        self.corkscrew_params = MotorParams(velocity=5.0, distance=500, accel=2.0, resolution=1000)
        self.left_fiber_params = MotorParams(velocity=20.0, distance=100_000, accel=5.0, resolution=1000)
        self.right_fiber_params = MotorParams(velocity=20.0, distance=-100_000, accel=5.0, resolution=1000)

        # GUI variables
        self.var_port = tk.StringVar(value=self.ctrl.port)
        self.var_baud = tk.IntVar(value=self.ctrl.baud)
        self.var_fiber_side = tk.StringVar(value="Left")

        self._build_ui()
        self.load_config()

        # Redirect stdout to log
        self.redirector = TextRedirector(self.log_text)
        sys.stdout = self.redirector

        self.protocol("WM_DELETE_WINDOW", self.on_close)

        # Bind arrow keys for jogging
        self.bind('<Left>', lambda e: self.on_jog_press(-1))
        self.bind('<Right>', lambda e: self.on_jog_press(1))
        self.bind('<KeyRelease-Left>', lambda e: self.on_jog_release(-1))
        self.bind('<KeyRelease-Right>', lambda e: self.on_jog_release(1))

    def _build_ui(self):
        self._build_connection_frame()
        self._build_motor_params_frame()
        self._build_control_frame()
        self._build_log_frame()

    def _build_connection_frame(self):
        frame = ttk.LabelFrame(self, text="Connection")
        frame.pack(fill="x", padx=10, pady=5)

        ttk.Label(frame, text="Port:").grid(row=0, column=0, sticky="e", padx=5, pady=2)
        ttk.Entry(frame, width=30, textvariable=self.var_port).grid(row=0, column=1, padx=5, pady=2)

        ttk.Label(frame, text="Baud:").grid(row=0, column=2, sticky="e", padx=5, pady=2)
        ttk.Entry(frame, width=10, textvariable=self.var_baud).grid(row=0, column=3, padx=5, pady=2)

        self.btn_connect = ttk.Button(frame, text="Connect", command=self.on_connect)
        self.btn_connect.grid(row=1, column=0, columnspan=2, pady=5)

        ttk.Button(frame, text="Save Settings", command=self.save_config).grid(row=1, column=4, padx=5, pady=5)

        self.lbl_status = ttk.Label(frame, text="Status: disconnected")
        self.lbl_status.grid(row=1, column=2, columnspan=2, sticky="w", padx=10)


    def _build_motor_params_frame(self):
        frame = ttk.LabelFrame(self, text="Motor Parameters")
        frame.pack(fill="x", padx=10, pady=5)

        # Headers
        ttk.Label(frame, text="Motor").grid(row=0, column=0, padx=5, pady=2)
        ttk.Label(frame, text="Velocity (rev/s)").grid(row=0, column=1, padx=5, pady=2)
        ttk.Label(frame, text="Distance (steps)").grid(row=0, column=2, padx=5, pady=2)
        ttk.Label(frame, text="Acceleration (rev/s²)").grid(row=0, column=3, padx=5, pady=2)
        ttk.Label(frame, text="Resolution (steps/rev)").grid(row=0, column=4, padx=5, pady=2)

        # Corkscrew parameters
        ttk.Label(frame, text="Corkscrew").grid(row=1, column=0, sticky="w", padx=5, pady=2)
        self.cork_vel = tk.StringVar(value=str(self.corkscrew_params.velocity))
        self.cork_dist = tk.StringVar(value=str(self.corkscrew_params.distance))
        self.cork_accel = tk.StringVar(value=str(self.corkscrew_params.accel))
        self.cork_res = tk.StringVar(value=str(self.corkscrew_params.resolution))

        ttk.Entry(frame, width=10, textvariable=self.cork_vel).grid(row=1, column=1, padx=5, pady=2)
        distance_entry = ttk.Entry(frame, width=10, textvariable=self.cork_dist)
        distance_entry.grid(row=1, column=2, padx=5, pady=2)
        ttk.Entry(frame, width=10, textvariable=self.cork_accel).grid(row=1, column=3, padx=5, pady=2)
        ttk.Entry(frame, width=10, textvariable=self.cork_res).grid(row=1, column=4, padx=5, pady=2)

        # Left & Right Fiber parameters (unchanged)
        ttk.Label(frame, text="Left Fiber").grid(row=2, column=0, sticky="w", padx=5, pady=2)
        self.left_vel = tk.StringVar(value=str(self.left_fiber_params.velocity))
        self.left_dist = tk.StringVar(value=str(self.left_fiber_params.distance))
        self.left_accel = tk.StringVar(value=str(self.left_fiber_params.accel))
        self.left_res = tk.StringVar(value=str(self.left_fiber_params.resolution))

        ttk.Entry(frame, width=10, textvariable=self.left_vel).grid(row=2, column=1, padx=5, pady=2)
        ttk.Entry(frame, width=10, textvariable=self.left_dist).grid(row=2, column=2, padx=5, pady=2)
        ttk.Entry(frame, width=10, textvariable=self.left_accel).grid(row=2, column=3, padx=5, pady=2)
        ttk.Entry(frame, width=10, textvariable=self.left_res).grid(row=2, column=4, padx=5, pady=2)

        ttk.Label(frame, text="Right Fiber").grid(row=3, column=0, sticky="w", padx=5, pady=2)
        self.right_vel = tk.StringVar(value=str(self.right_fiber_params.velocity))
        self.right_dist = tk.StringVar(value=str(self.right_fiber_params.distance))
        self.right_accel = tk.StringVar(value=str(self.right_fiber_params.accel))
        self.right_res = tk.StringVar(value=str(self.right_fiber_params.resolution))

        ttk.Entry(frame, width=10, textvariable=self.right_vel).grid(row=3, column=1, padx=5, pady=2)
        ttk.Entry(frame, width=10, textvariable=self.right_dist).grid(row=3, column=2, padx=5, pady=2)
        ttk.Entry(frame, width=10, textvariable=self.right_accel).grid(row=3, column=3, padx=5, pady=2)
        ttk.Entry(frame, width=10, textvariable=self.right_res).grid(row=3, column=4, padx=5, pady=2)

    def _build_control_frame(self):
        frame = ttk.LabelFrame(self, text="Copper/Fiber Winding Control")
        frame.pack(fill="x", padx=10, pady=5)

        # Instructions
        instruction_frame = ttk.Frame(frame)
        instruction_frame.grid(row=0, column=0, columnspan=4, pady=5, sticky="w")

        ttk.Label(instruction_frame, text="WORKFLOW:", font=("TkDefaultFont", 9, "bold")).pack(anchor="w")
        ttk.Label(instruction_frame, text="1. Set motor parameters (velocity, distance, etc.)",
                 font=("TkDefaultFont", 9)).pack(anchor="w", padx=10)
        ttk.Label(instruction_frame, text="2. Wind copper coil by hand (one pass across magnet)",
                 font=("TkDefaultFont", 9)).pack(anchor="w", padx=10)
        ttk.Label(instruction_frame, text="3. Select fiber winder side and click 'Run Fiber Pass'",
                 font=("TkDefaultFont", 9)).pack(anchor="w", padx=10)

        # Fiber winder selection
        ttk.Label(frame, text="Select Fiber Winder:").grid(row=1, column=0, padx=5, pady=10, sticky="w")
        ttk.Radiobutton(frame, text="Left Fiber Winder",
                       variable=self.var_fiber_side, value="Left").grid(row=1, column=1, sticky="w", padx=5)
        ttk.Radiobutton(frame, text="Right Fiber Winder",
                       variable=self.var_fiber_side, value="Right").grid(row=2, column=1, sticky="w", padx=5)

        # Control buttons
        self.btn_run = ttk.Button(frame, text="RUN FIBER PASS", command=self.on_run, state="disabled")
        self.btn_run.grid(row=1, column=2, rowspan=2, padx=20, pady=5)

        self.btn_stop = tk.Button(frame, text="EMERGENCY STOP",
                                 fg="white", bg="red", activebackground="darkred",
                                 command=self.on_estop, font=("TkDefaultFont", 10, "bold"))
        self.btn_stop.grid(row=1, column=3, rowspan=2, padx=10, pady=5)

        # Status
        self.lbl_operation_status = ttk.Label(frame, text="Connect to start")
        self.lbl_operation_status.grid(row=3, column=0, columnspan=4, pady=5)


    def _build_log_frame(self):
        frame = ttk.LabelFrame(self, text="Communication Log")
        frame.pack(fill="both", expand=True, padx=10, pady=5)

        self.log_text = scrolledtext.ScrolledText(frame, height=8, font=("Consolas", 9))
        self.log_text.pack(fill="both", expand=True, padx=5, pady=5)

        ttk.Button(frame, text="Clear Log", command=self.clear_log).pack(pady=2)

    def _update_motor_params(self):
        """Update motor parameters from GUI inputs"""
        try:
            self.corkscrew_params.velocity = float(self.cork_vel.get())
            self.corkscrew_params.distance = int(self.cork_dist.get())
            self.corkscrew_params.accel = float(self.cork_accel.get())
            self.corkscrew_params.resolution = int(self.cork_res.get())

            self.left_fiber_params.velocity = float(self.left_vel.get())
            self.left_fiber_params.distance = int(self.left_dist.get())
            self.left_fiber_params.accel = float(self.left_accel.get())
            self.left_fiber_params.resolution = int(self.left_res.get())

            self.right_fiber_params.velocity = float(self.right_vel.get())
            self.right_fiber_params.distance = int(self.right_dist.get())
            self.right_fiber_params.accel = float(self.right_accel.get())
            self.right_fiber_params.resolution = int(self.right_res.get())
            return True
        except ValueError as e:
            messagebox.showerror("Parameter Error", f"Invalid parameter value: {e}")
            return False


    def on_connect(self):
        if not self.ctrl.is_connected:
            self.ctrl.port = self.var_port.get()
            self.ctrl.baud = self.var_baud.get()

            if self.ctrl.connect():
                self.btn_connect.config(text="Disconnect")
                self.btn_run.config(state="normal")
                self.lbl_status.config(text="Status: connected")
                self.lbl_operation_status.config(text="Ready")
                print("Connection established successfully.")
            else:
                messagebox.showerror("Connection Error", "Failed to connect to serial port.")
        else:
            self.ctrl.disconnect()
            self.btn_connect.config(text="Connect")
            self.btn_run.config(state="disabled")
            self.lbl_status.config(text="Status: disconnected")
            self.is_running = False


    def on_run(self):
        if not self.ctrl.is_connected:
            messagebox.showerror("Error", "Not connected to serial port.")
            return

        if self.is_running:
            messagebox.showwarning("Warning", "Fiber pass already running.")
            return

        if not self._update_motor_params():
            return

        # Ask user to confirm they've completed the copper winding
        result = messagebox.askyesno("Confirm Copper Winding",
                                   "Have you completed winding the copper coil by hand?\n\n"
                                   "Click 'Yes' to start the fiber pass.")
        if not result:
            return

        self.is_running = True
        self.btn_run.config(state="disabled")
        self.lbl_operation_status.config(text="Running fiber pass...")

        def run_thread():
            try:
                if self.var_fiber_side.get() == "Left":
                    self.ctrl.run_left_fiber_pass(self.corkscrew_params, self.left_fiber_params)
                else:
                    self.ctrl.run_right_fiber_pass(self.corkscrew_params, self.right_fiber_params)

                self.after(0, self.on_operation_complete)
            except Exception as e:
                self.after(0, lambda: self.on_operation_error(str(e)))

        threading.Thread(target=run_thread, daemon=True).start()

    def on_operation_complete(self):
        self.is_running = False
        self.btn_run.config(state="normal")
        self.lbl_operation_status.config(text="Fiber pass complete! Ready for next copper winding (by hand)")
        print("Fiber pass completed successfully.")
        print("You can now wind the next copper pass by hand.")

    def on_operation_error(self, error_msg: str):
        self.is_running = False
        self.btn_run.config(state="normal")
        self.lbl_operation_status.config(text="Error occurred - Ready for retry")
        messagebox.showerror("Operation Error", f"Fiber pass failed: {error_msg}")

    def on_estop(self):
        if self.ctrl.is_connected:
            self.ctrl.estop()
            self.is_running = False
            self.jogging_direction = 0
            self.btn_run.config(state="normal")
            self.lbl_operation_status.config(text="EMERGENCY STOPPED - Check system before continuing")
        else:
            messagebox.showwarning("Warning", "Not connected to send emergency stop.")

    def on_jog_press(self, direction):
        if not self.ctrl.is_connected: return
        if self.is_running: return # Don't jog while automated pass is running

        # Prevent key repeat from sending multiple commands
        if self.jogging_direction == direction: return

        self.jogging_direction = direction

        # Use current corkscrew velocity from UI, default to 1.0 if invalid
        try:
            vel = float(self.cork_vel.get())
        except:
            vel = 1.0

        self.ctrl.jog(MotorNode.CORKSCREW, direction, vel)

    def on_jog_release(self, direction):
        # Only stop if we are currently jogging in the released direction
        if self.jogging_direction == direction:
             self.ctrl.stop_motor(MotorNode.CORKSCREW)
             self.jogging_direction = 0

    def clear_log(self):
        self.log_text.delete(1.0, tk.END)

    def on_close(self):
        if self.ctrl.is_connected:
            self.ctrl.disconnect()
        sys.stdout = sys.__stdout__
        self.destroy()

    def load_config(self):
        if not os.path.exists(CONFIG_FILE):
            print(f"Config file {CONFIG_FILE} not found. Using defaults.")
            return

        try:
            with open(CONFIG_FILE, 'r') as f:
                config = json.load(f)

            self.var_port.set(config.get("port", self.var_port.get()))
            self.var_baud.set(config.get("baud", self.var_baud.get()))
            self.var_fiber_side.set(config.get("fiber_side", "Left"))

            # Helper to load motor params
            def load_motor(prefix, key):
                data = config.get(key, {})
                if not data: return
                getattr(self, f"{prefix}_vel").set(data.get("vel", "2.0"))
                getattr(self, f"{prefix}_dist").set(data.get("dist", "2500"))
                getattr(self, f"{prefix}_accel").set(data.get("accel", "0.3"))
                getattr(self, f"{prefix}_res").set(data.get("res", "1000"))

            load_motor("cork", "corkscrew")
            load_motor("left", "left_fiber")
            load_motor("right", "right_fiber")

            print(f"Loaded configuration from {CONFIG_FILE}")

        except Exception as e:
            print(f"Error loading config: {e}")
            messagebox.showwarning("Config Error", f"Failed to load config: {e}")

    def save_config(self):
        config = {
            "port": self.var_port.get(),
            "baud": self.var_baud.get(),
            "fiber_side": self.var_fiber_side.get(),
            "corkscrew": {
                "vel": self.cork_vel.get(),
                "dist": self.cork_dist.get(),
                "accel": self.cork_accel.get(),
                "res": self.cork_res.get()
            },
            "left_fiber": {
                "vel": self.left_vel.get(),
                "dist": self.left_dist.get(),
                "accel": self.left_accel.get(),
                "res": self.left_res.get()
            },
            "right_fiber": {
                "vel": self.right_vel.get(),
                "dist": self.right_dist.get(),
                "accel": self.right_accel.get(),
                "res": self.right_res.get()
            }
        }

        try:
            with open(CONFIG_FILE, 'w') as f:
                json.dump(config, f, indent=4)
            print(f"Configuration saved to {CONFIG_FILE}")
            messagebox.showinfo("Saved", f"Settings saved to {CONFIG_FILE}")
        except Exception as e:
            print(f"Error saving config: {e}")
            messagebox.showerror("Save Error", f"Failed to save config: {e}")

def main():
    try:
        app = WinderGUI()
        app.mainloop()
    except KeyboardInterrupt:
        print("\nApplication interrupted by user.")
    except Exception as exc:
        print(f"Application error: {exc}")
        messagebox.showerror("Application Error", f"Fatal error: {exc}")

if __name__ == "__main__":
    main()
