import customtkinter as ctk
import subprocess
import threading
import os

# Basic appearance configuration
ctk.set_appearance_mode("System")  # Modes: "System" (standard), "Dark", "Light"
ctk.set_default_color_theme("blue")  # Themes: "blue" (standard), "green", "dark-blue"

class RocketdooGUI(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.title("🚀 Rocketdoo Control Panel")
        self.geometry("700x500")
        
        # Grid layout (1 row, 2 columns)
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        # --- SIDE PANEL (Buttons) ---
        self.sidebar_frame = ctk.CTkFrame(self, width=200, corner_radius=0)
        self.sidebar_frame.grid(row=0, column=0, sticky="nsew")
        self.sidebar_frame.grid_rowconfigure(6, weight=1) # Bottom spacing

        self.logo_label = ctk.CTkLabel(self.sidebar_frame, text="Rocketdoo", font=ctk.CTkFont(size=20, weight="bold"))
        self.logo_label.grid(row=0, column=0, padx=20, pady=(20, 10))

        # Command buttons
        self.btn_scaffold = ctk.CTkButton(self.sidebar_frame, text="1. Scaffold", command=lambda: self.run_command(["rkd", "scaffold"]))
        self.btn_scaffold.grid(row=1, column=0, padx=20, pady=10)

        self.btn_init = ctk.CTkButton(self.sidebar_frame, text="2. Init", command=lambda: self.run_command(["rkd", "init"]))
        self.btn_init.grid(row=2, column=0, padx=20, pady=10)

        self.btn_up = ctk.CTkButton(self.sidebar_frame, text="▶ Start (Up)", command=lambda: self.run_command(["rkd", "up", "-d"]), fg_color="green")
        self.btn_up.grid(row=3, column=0, padx=20, pady=10)

        self.btn_stop = ctk.CTkButton(self.sidebar_frame, text="Stop", command=lambda: self.run_command(["rkd", "stop"]), fg_color="orange")
        self.btn_stop.grid(row=4, column=0, padx=20, pady=10)
        
        self.btn_down = ctk.CTkButton(self.sidebar_frame, text="Destroy (Down)", command=lambda: self.run_command(["rkd", "down", "-v"]), fg_color="red")
        self.btn_down.grid(row=5, column=0, padx=20, pady=10)

        # --- MAIN PANEL (Console) ---
        self.console_frame = ctk.CTkFrame(self)
        self.console_frame.grid(row=0, column=1, padx=20, pady=20, sticky="nsew")
        self.console_frame.grid_columnconfigure(0, weight=1)
        self.console_frame.grid_rowconfigure(0, weight=1)

        self.console_textbox = ctk.CTkTextbox(self.console_frame, font=ctk.CTkFont(family="Consolas", size=12))
        self.console_textbox.grid(row=0, column=0, sticky="nsew")
        self.console_textbox.insert("0.0", "Welcome to Rocketdoo GUI.\nReady to execute commands...\n\n")
        self.console_textbox.configure(state="disabled")

    def log_to_console(self, text):
        """Writes text to the interface console."""
        self.console_textbox.configure(state="normal")
        self.console_textbox.insert("end", text)
        self.console_textbox.see("end")  # Auto-scroll to end
        self.console_textbox.configure(state="disabled")

    def run_command(self, cmd_list):
        """Executes a command in a separate thread to avoid blocking the GUI."""
        self.log_to_console(f"\n> {' '.join(cmd_list)}\n")
        
        # Disable buttons while running a command (optional, recommended for init/scaffold)
        
        def task():
            try:
                # Use subprocess to call the real rkd CLI
                process = subprocess.Popen(
                    cmd_list,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                    universal_newlines=True
                )
                
                # Read output in real-time
                for line in process.stdout:
                    # Use 'after' to safely update GUI from the thread
                    self.after(0, self.log_to_console, line)
                    
                process.wait()
                self.after(0, self.log_to_console, f"\n[Process finished with exit code {process.returncode}]\n")
            except Exception as e:
                self.after(0, self.log_to_console, f"\n[Execution error: {str(e)}]\n")

        # Start thread
        threading.Thread(target=task, daemon=True).start()

def start_gui():
    app = RocketdooGUI()
    app.mainloop()

if __name__ == "__main__":
    start_gui()