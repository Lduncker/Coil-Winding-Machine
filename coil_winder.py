#!/usr/bin/env python3
"""
Enhanced Fiber‑winder GUI with automatic magnet end detection via limit switches.

New Features:
- Limit switch integration for automatic end detection
- Homing sequence to establish reference position
- Automatic distance calculation based on magnet length
- Safety checks to prevent overrun
"""

import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox
import threading, sys, serial, time
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

@dataclass
class MagnetLimits:
    left_limit: int = 0       # Position of left end (steps)
    right_limit: int = 0      # Position of right end (steps)
    is_homed: bool = False    # Whether homing has been completed
    total_length: int = 0     # Total magnet length in steps

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
        self.magnet_limits = MagnetLimits()

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

    def _check_limit_switches(self, node: MotorNode) -> dict:
        """Check status of limit switches for a motor"""
        try:
            # Read input status - this depends on your Parker model
            reply = self._tx(f"{node.value}IS")
            # Parse limit switch status from input register
            # This is model-specific - adjust according to your setup

            # Example parsing (adjust for your specific Parker model):
            # Assuming IS returns a hex value where bits indicate switch states
            status_val = int(reply.split('IS')[1], 16) if 'IS' in reply else 0

            return {
                'left_limit': bool(status_val & 0x01),   # Bit 0 = left limit
                'right_limit': bool(status_val & 0x02),  # Bit 1 = right limit
                'home_switch': bool(status_val & 0x04)   # Bit 2 = home switch
            }
        except:
            return {'left_limit': False, 'right_limit': False, 'home_switch': False}

    def home_corkscrew(self) -> bool:
        """Home the corkscrew axis to establish reference position"""
        try:
            print("Starting corkscrew homing sequence...")

            # Move towards home switch at slow speed
            self._tx(f"{MotorNode.CORKSCREW.value}MR1000")  # Set resolution
            self._tx(f"{MotorNode.CORKSCREW.value}A0.5")    # Slow acceleration
            self._tx(f"{MotorNode.CORKSCREW.value}V1")      # Slow velocity

            # Start move towards home (negative direction)
            self._tx(f"{MotorNode.CORKSCREW.value}D-50000")  # Large distance
            self._tx(f"{MotorNode.CORKSCREW.value}G")

            # Wait for home switch activation
            timeout = 30  # 30 second timeout
            start_time = time.time()

            while time.time() - start_time < timeout:
                switches = self._check_limit_switches(MotorNode.CORKSCREW)
                if switches['home_switch']:
                    # Stop when home switch is triggered
                    self._tx("Z")  # Emergency stop
                    time.sleep(0.1)

                    # Set current position as zero
                    self._tx(f"{MotorNode.CORKSCREW.value}SP0")
                    print("Corkscrew homed successfully at position 0")

                    # Now find the limits
                    return self._find_magnet_limits()

                time.sleep(0.1)

            print("Homing timeout - home switch not found")
            return False

        except Exception as exc:
            print(f"Homing error: {exc}")
            return False

    def _find_magnet_limits(self) -> bool:
        """Find the left and right limits of the magnet"""
        try:
            print("Finding magnet limits...")

            # Move to left limit
            self._tx(f"{MotorNode.CORKSCREW.value}V2")      # Medium velocity
            self._tx(f"{MotorNode.CORKSCREW.value}D-25000") # Move left
            self._tx(f"{MotorNode.CORKSCREW.value}G")

            # Wait for left limit switch
            timeout = 20
            start_time = time.time()

            while time.time() - start_time < timeout:
                switches = self._check_limit_switches(MotorNode.CORKSCREW)
                if switches['left_limit']:
                    self._tx("Z")  # Stop
                    time.sleep(0.1)
                    self.magnet_limits.left_limit = self._get_position(MotorNode.CORKSCREW)
                    print(f"Left limit found at position: {self.magnet_limits.left_limit}")
                    break
                time.sleep(0.1)
            else:
                print("Left limit switch not found")
                return False

            # Move to right limit
            self._tx(f"{MotorNode.CORKSCREW.value}D50000")  # Move right
            self._tx(f"{MotorNode.CORKSCREW.value}G")

            start_time = time.time()
            while time.time() - start_time < timeout:
                switches = self._check_limit_switches(MotorNode.CORKSCREW)
                if switches['right_limit']:
                    self._tx("Z")  # Stop
                    time.sleep(0.1)
                    self.magnet_limits.right_limit = self._get_position(MotorNode.CORKSCREW)
                    print(f"Right limit found at position: {self.magnet_limits.right_limit}")
                    break
                time.sleep(0.1)
            else:
                print("Right limit switch not found")
                return False

            # Calculate total length
            self.magnet_limits.total_length = abs(self.magnet_limits.right_limit - self.magnet_limits.left_limit)
            self.magnet_limits.is_homed = True

            print(f"Magnet limits established:")
            print(f"  Left: {self.magnet_limits.left_limit} steps")
            print(f"  Right: {self.magnet_limits.right_limit} steps")
            print(f"  Total length: {self.magnet_limits.total_length} steps")

            # Return to home position
            self._tx(f"{MotorNode.CORKSCREW.value}D0")
            self._tx(f"{MotorNode.CORKSCREW.value}G")

            return True

        except Exception as exc:
            print(f"Limit finding error: {exc}")
            return False

    def _queue_move(self, node: MotorNode, params: MotorParams):
        # Set resolution first
        self._tx(f"{node.value}MR{params.resolution}")

        # For corkscrew, check if we're within limits
        if node == MotorNode.CORKSCREW and self.magnet_limits.is_homed:
            current_pos = self._get_position(node)
            target_pos = current_pos + params.distance

            # Safety check
            if (target_pos < self.magnet_limits.left_limit or
                target_pos > self.magnet_limits.right_limit):
                print(f"WARNING: Target position {target_pos} exceeds magnet limits!")
                print(f"Limiting to safe range: {self.magnet_limits.left_limit} to {self.magnet_limits.right_limit}")

                # Adjust distance to stay within limits
                if target_pos < self.magnet_limits.left_limit:
                    params.distance = self.magnet_limits.left_limit - current_pos
                else:
                    params.distance = self.magnet_limits.right_limit - current_pos

        # Velocity and acceleration are always positive
        vel = abs(int(params.velocity))
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

    def calculate_full_pass_distance(self) -> int:
        """Calculate distance for a full pass across the magnet"""
        if not self.magnet_limits.is_homed:
            return 2500  # Default fallback
        return self.magnet_limits.total_length

    def run_left_fiber_pass(self, corkscrew_params: MotorParams, left_fiber_params: MotorParams):
        """Run one fiber pass using left fiber winder + corkscrew"""
        try:
            print("Starting LEFT FIBER PASS (Corkscrew + Left Fiber)")

            # Use calculated distance if homed
            if self.magnet_limits.is_homed:
                corkscrew_params.distance = self.calculate_full_pass_distance()
                print(f"Using calculated corkscrew distance: {corkscrew_params.distance} steps")

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

            # Use calculated distance if homed
            if self.magnet_limits.is_homed:
                corkscrew_params.distance = -self.calculate_full_pass_distance()  # Negative for return
                print(f"Using calculated corkscrew distance: {corkscrew_params.distance} steps")

            self._queue_move(MotorNode.CORKSCREW, corkscrew_params)
            self._queue_move(MotorNode.RIGHT_FIBER, right_fiber_params)

            self._go(MotorNode.CORKSCREW)
            self._go(MotorNode.RIGHT_FIBER)

            print("Right fiber pass started")
        except Exception as exc:
            print(f"Right fiber pass error: {exc}")

    def estop(self):
        try:
            self._tx("Z", pause=0)
            print(">>> EMERGENCY STOP (Z) <<<")
        except Exception as exc:
            print(f"E‑stop error: {exc}")

# ─── Enhanced GUI with Homing Features ───────────────────────────────────────
class WinderGUI(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Enhanced Copper/Fiber Winder Control")
        self.geometry("900x750")

        self.ctrl = ParkerMotorController("/dev/ttyUSB0", 9600)
        self.is_running = False

        # Motor parameters
        self.corkscrew_params = MotorParams(velocity=5.0, distance=500, accel=2.0, resolution=1000)
        self.left_fiber_params = MotorParams(velocity=20.0, distance=100_000, accel=5.0, resolution=1000)
        self.right_fiber_params = MotorParams(velocity=20.0, distance=-100_000, accel=5.0, resolution=1000)

        # GUI variables
        self.var_port = tk.StringVar(value=self.ctrl.port)
        self.var_baud = tk.IntVar(value=self.ctrl.baud)
        self.var_fiber_side = tk.StringVar(value="Left")

        self._build_ui()

        # Redirect stdout to log
        self.redirector = TextRedirector(self.log_text)
        sys.stdout = self.redirector

        self.protocol("WM_DELETE_WINDOW", self.on_close)

    def _build_ui(self):
        self._build_connection_frame()
        self._build_homing_frame()
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

        self.lbl_status = ttk.Label(frame, text="Status: disconnected")
        self.lbl_status.grid(row=1, column=2, columnspan=2, sticky="w", padx=10)

    def _build_homing_frame(self):
        frame = ttk.LabelFrame(self, text="Magnet Limits & Homing")
        frame.pack(fill="x", padx=10, pady=5)

        # Homing button
        self.btn_home = ttk.Button(frame, text="HOME CORKSCREW & FIND LIMITS",
                                  command=self.on_home, state="disabled")
        self.btn_home.grid(row=0, column=0, padx=5, pady=5)

        # Status display
        self.lbl_home_status = ttk.Label(frame, text="Status: Not homed")
        self.lbl_home_status.grid(row=0, column=1, sticky="w", padx=10)

        # Limits display
        self.lbl_limits = ttk.Label(frame, text="Magnet limits: Unknown")
        self.lbl_limits.grid(row=1, column=0, columnspan=2, sticky="w", padx=5, pady=2)

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
        ttk.Label(frame, text="Corkscrew*").grid(row=1, column=0, sticky="w", padx=5, pady=2)
        self.cork_vel = tk.StringVar(value=str(self.corkscrew_params.velocity))
        self.cork_dist = tk.StringVar(value=str(self.corkscrew_params.distance))
        self.cork_accel = tk.StringVar(value=str(self.corkscrew_params.accel))
        self.cork_res = tk.StringVar(value=str(self.corkscrew_params.resolution))

        ttk.Entry(frame, width=10, textvariable=self.cork_vel).grid(row=1, column=1, padx=5, pady=2)
        distance_entry = ttk.Entry(frame, width=10, textvariable=self.cork_dist)
        distance_entry.grid(row=1, column=2, padx=5, pady=2)
        ttk.Entry(frame, width=10, textvariable=self.cork_accel).grid(row=1, column=3, padx=5, pady=2)
        ttk.Entry(frame, width=10, textvariable=self.cork_res).grid(row=1, column=4, padx=5, pady=2)

        # Note about automatic distance
        ttk.Label(frame, text="* Distance auto-calculated if homed",
                 font=("TkDefaultFont", 8), foreground="blue").grid(row=1, column=5, sticky="w", padx=5)

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

        ttk.Label(instruction_frame, text="ENHANCED WORKFLOW:", font=("TkDefaultFont", 9, "bold")).pack(anchor="w")
        ttk.Label(instruction_frame, text="1. Connect and run HOMING to establish magnet limits",
                 font=("TkDefaultFont", 9)).pack(anchor="w", padx=10)
        ttk.Label(instruction_frame, text="2. Wind copper coil by hand (one pass across magnet)",
                 font=("TkDefaultFont", 9)).pack(anchor="w", padx=10)
        ttk.Label(instruction_frame, text="3. Select fiber winder side and click 'Run Fiber Pass'",
                 font=("TkDefaultFont", 9)).pack(anchor="w", padx=10)
        ttk.Label(instruction_frame, text="4. System automatically uses precise magnet limits",
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
        self.lbl_operation_status = ttk.Label(frame, text="Connect and home system first")
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

    def _update_limits_display(self):
        """Update the limits display in the GUI"""
        if self.ctrl.magnet_limits.is_homed:
            limits_text = (f"Left: {self.ctrl.magnet_limits.left_limit}, "
                         f"Right: {self.ctrl.magnet_limits.right_limit}, "
                         f"Length: {self.ctrl.magnet_limits.total_length} steps")
            self.lbl_limits.config(text=f"Magnet limits: {limits_text}")
            self.lbl_home_status.config(text="Status: Homed ✓", foreground="green")
        else:
            self.lbl_limits.config(text="Magnet limits: Not homed")
            self.lbl_home_status.config(text="Status: Not homed", foreground="red")

    def on_connect(self):
        if not self.ctrl.is_connected:
            self.ctrl.port = self.var_port.get()
            self.ctrl.baud = self.var_baud.get()

            if self.ctrl.connect():
                self.btn_connect.config(text="Disconnect")
                self.btn_home.config(state="normal")
                self.lbl_status.config(text="Status: connected")
                self.lbl_operation_status.config(text="Run homing sequence to establish limits")
                print("Connection established successfully.")
            else:
                messagebox.showerror("Connection Error", "Failed to connect to serial port.")
        else:
            self.ctrl.disconnect()
            self.btn_connect.config(text="Connect")
            self.btn_home.config(state="disabled")
            self.btn_run.config(state="disabled")
            self.lbl_status.config(text="Status: disconnected")
            self.is_running = False

    def on_home(self):
        if not self.ctrl.is_connected:
            messagebox.showerror("Error", "Not connected to serial port.")
            return

        # Confirm homing operation
        result = messagebox.askyesno("Confirm Homing",
                                   "This will move the corkscrew to find home position and magnet limits.\n\n"
                                   "Ensure the travel path is clear!\n\n"
                                   "Continue with homing?")
        if not result:
            return

        self.btn_home.config(state="disabled")
        self.lbl_home_status.config(text="Status: Homing in progress...", foreground="orange")

        def home_thread():
            try:
                success = self.ctrl.home_corkscrew()
                self.after(0, lambda: self.on_home_complete(success))
            except Exception as e:
                self.after(0, lambda: self.on_home_error(str(e)))

        threading.Thread(target=home_thread, daemon=True).start()

    def on_home_complete(self, success: bool):
        self.btn_home.config(state="normal")
        if success:
            self.btn_run.config(state="normal")
            self.lbl_operation_status.config(text="Ready for copper winding (by hand)")
            print("Homing completed successfully!")
        else:
            self.lbl_operation_status.config(text="Homing failed - check limit switches")
            messagebox.showerror("Homing Error", "Failed to complete homing sequence. Check limit switches and connections.")

        self._update_limits_display()

    def on_home_error(self, error_msg: str):
        self.btn_home.config(state="normal")
        self.lbl_home_status.config(text="Status: Homing error", foreground="red")
        self.lbl_operation_status.config(text="Homing error - check system")
        messagebox.showerror("Homing Error", f"Homing failed: {error_msg}")

    def on_run(self):
        if not self.ctrl.is_connected:
            messagebox.showerror("Error", "Not connected to serial port.")
            return

        if not self.ctrl.magnet_limits.is_homed:
            messagebox.showwarning("Warning", "System not homed. Run homing sequence first for precise limits.")
            # Allow manual operation but warn user

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
            self.btn_run.config(state="normal")
            self.lbl_operation_status.config(text="EMERGENCY STOPPED - Check system before continuing")
        else:
            messagebox.showwarning("Warning", "Not connected to send emergency stop.")

    def clear_log(self):
        self.log_text.delete(1.0, tk.END)

    def on_close(self):
        if self.ctrl.is_connected:
            self.ctrl.disconnect()
        sys.stdout = sys.__stdout__
        self.destroy()

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
