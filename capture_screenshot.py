import tkinter as tk
from PIL import ImageGrab
import sys
import traceback

try:
    # Create root window
    root = tk.Tk()

    # Import and initialize the app
    from MyKTV import MyKTVApp
    app = MyKTVApp(root)

    # Schedule screenshot after UI is fully rendered
    def capture_and_exit():
        try:
            # Wait for window to be fully drawn
            root.update_idletasks()
            
            # Get window bounds
            x = root.winfo_rootx()
            y = root.winfo_rooty()
            w = root.winfo_width()
            h = root.winfo_height()
            
            print(f"Capturing window at ({x}, {y}) size ({w}x{h})")
            
            # Capture window region
            screenshot = ImageGrab.grab(bbox=(x, y, x+w, y+h))
            screenshot.save(r'N:\GitHub\YTToMKV\ktv_redesign_preview.png')
            print('Screenshot saved successfully to ktv_redesign_preview.png')
        except Exception as e:
            print(f'Screenshot error: {e}')
            traceback.print_exc()
        finally:
            root.destroy()

    # Schedule after 3 seconds to let UI fully render
    root.after(3000, capture_and_exit)

    # Run the app
    root.mainloop()
    
except Exception as e:
    print(f"Fatal error: {e}")
    traceback.print_exc()
