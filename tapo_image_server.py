from http.server import SimpleHTTPRequestHandler, HTTPServer
import os
import urllib.parse
import cv2
import platform
import socket
import xml.etree.ElementTree as ET
import re

### Secret stuff
from printer_camera_secrets import PRINTER_CAMERA

PORT = 80
# Change this path to the actual folder where your PNG images are saved
IMAGE_DIR = r"./images" 

# Standard universal ONVIF WS-Discovery probe payload
ONVIF_PROBE = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<Envelope xmlns:tds="http://onvif.org" '
    'xmlns="http://xmlsoap.org">'
    '<Header><MessageID xmlns="http://xmlsoap.org"> '
    'uuid:96645366-1bf4-11b2-aa45-000c29188e00</MessageID>'
    '<To xmlns="http://xmlsoap.org">'
    'urn:schemas-xmlsoap-org:btorg:service:publication:2005:04</To>'
    '<Action xmlns="http://xmlsoap.org">'
    'http://xmlsoap.org/Probe</Action></Header>'
    '<Body><Probe xmlns="http://xmlsoap.org">'
    '<Types>tds:Device</Types></Probe></Body></Envelope>'
)


def fetch_mac_from_arp(ip):
    """Cross-platform hardware MAC extraction using local OS kernel mappings."""
    current_os = platform.system().lower()
    
    # ------------------ WINDOWS STRATEGY ------------------
    if "windows" in current_os:
        try:
            with os.popen(f"arp -a {ip}") as response:
                output = response.read()
            # Regex for Windows dash-separated format (XX-XX-XX-XX-XX-XX)
            match = re.search(r"([0-9a-fA-F]{2}[:-][0-9a-fA-F]{2}[:-][0-9a-fA-F]{2}[:-][0-9a-fA-F]{2}[:-][0-9a-fA-F]{2}[:-][0-9a-fA-F]{2})", output)
            if match:
                return match.group(1).replace("-", ":").upper()
        except Exception:
           pass

    # ------------------- LINUX STRATEGY -------------------
    elif "linux" in current_os:
        try:
            # Native Linux approach: read the proc file system directly instead of spawning sub-shells
            if os.path.exists("/proc/net/arp"):
                with open("/proc/net/arp", "r") as f:
                    for line in f:
                        if ip in line:
                            parts = line.split()
                            # In /proc/net/arp, the MAC address is traditionally the 4th item in the row
                            if len(parts) >= 4 and ":" in parts[3]:
                                return parts[3].upper()
            
            # Fallback method using standard Linux 'arp -n' command
            with os.popen(f"arp -n {ip}") as response:
                output = response.read()
            match = re.search(r"([0-9a-fA-F]{2}:[0-9a-fA-F]{2}:[0-9a-fA-F]{2}:[0-9a-fA-F]{2}:[0-9a-fA-F]{2}:[0-9a-fA-F]{2})", output)
            if match:
                return match.group(1).upper()
        except Exception:
            pass

    return "UNKNOWN MAC"


def discover_tapo_hardware():
    print("Scanning network for Tapo cameras... (Waiting 3 seconds)")
    
    client = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    client.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    client.settimeout(3.0)
    
    client.sendto(ONVIF_PROBE.encode('utf-8'), ('255.255.255.255', 3702))
    cameras_found = set()
    cameras = {}

    try:
        while True:
            data, addr = client.recvfrom(4096)
            ip_address = addr[0]  # Isolate just the IP address string
            
            if ip_address in cameras_found:
                continue
                
            #try:
            raw_xml = data.decode('utf-8', errors='ignore')
            
            # Check for standard Tapo / C120 network signatures
            if "tapo" in raw_xml.lower() or "hardware/c120" in raw_xml.lower():
                root = ET.fromstring(raw_xml)
                mac_address = "Unknown MAC"

                mac_address = fetch_mac_from_arp(ip_address)

                cameras_found.add(ip_address)
                cameras[mac_address] = ip_address

                print(f"\n[FOUND] Camera Details:")
                print(f"  IP Address : {ip_address}")
                print(f"  MAC Address: {mac_address}")
                
            #except Exception:
            #    continue
                
    except socket.timeout:
        pass
    finally:
        client.close()
        
    print(f"\nScan complete. Discovered {len(cameras_found)} camera(s).")
    return cameras


def capture_snapshot(tapo_ip, tapo_user, tapo_pass, capture_name):

    # Tapo C120 RTSP Stream Target ('stream1' = 2K resolution, 'stream2' = 720p)
    RTSP_URL = f"rtsp://{tapo_user}:{tapo_pass}@{tapo_ip}:554/stream1"
    # =======================================================

    """Connects to the RTSP stream, grabs a fresh frame, and overwrites the target file."""
    
    cap = cv2.VideoCapture(RTSP_URL)
    
    if not cap.isOpened():
        print("Error: Could not connect to the Tapo RTSP stream.")
        return

    try:
        # Flush the internal buffer to ensure the photo is strictly real-time
        for _ in range(5):
            cap.grab()
            
        success, frame = cap.read()
        
        if success:
            filepath = os.path.join(IMAGE_DIR, capture_name)
            
            # cv2.imwrite automatically overwrites the existing file
            cv2.imwrite(filepath, frame, [cv2.IMWRITE_PNG_COMPRESSION, 1])
            print(f"Success: Overwrote {filepath}")
        else:
            print("Error: Failed to retrieve frame from stream.")
            
    except Exception as e:
        print(f"An unexpected error occurred during capture: {e}")
        
    finally:
        cap.release()


class ImageServiceHandler(SimpleHTTPRequestHandler):
    def do_GET(self):
        # Unquote URL to handle spaces or special characters safely
        url_path = urllib.parse.unquote(self.path)
        
        # Check if the request starts with the required /images/ prefix
        if url_path.startswith('/images/'):
            # Extract the {serialno} from the path
            serial_no = url_path[len('/images/'):]
            
            # Prevent directory traversal attacks (e.g., ../../etc/passwd)
            if ".." in serial_no or serial_no.startswith(('/', '\\')):
                self.send_error(400, "Bad Request: Invalid serial number path.")
                return
            
            # Automatically append the .png extension to the requested name
            filename = f"{serial_no}.png"

            # Combine the base directory path with the generated filename
            file_path = os.path.join(IMAGE_DIR, filename)

            # Do we know this printer, and have a camera for it?
            if serial_no in PRINTER_CAMERA:

                # remove old image
                if os.path.exists(file_path) and os.path.isfile(file_path):
                    os.remove(file_path)

                if PRINTER_CAMERA[serial_no]["IP_ADDRESS"]:

                    capture_snapshot(tapo_ip = PRINTER_CAMERA[serial_no]["IP_ADDRESS"],
                        tapo_user = PRINTER_CAMERA[serial_no]["USER"],
                        tapo_pass = PRINTER_CAMERA[serial_no]["PASSWORD"],
                        capture_name = filename)
            
            
            # Check if the .png file actually exists on the hard drive
            if os.path.exists(file_path) and os.path.isfile(file_path):
                try:
                    with open(file_path, 'rb') as f:
                        content = f.read()
                    
                    # Send response headers explicitly stating it is a PNG image
                    self.send_response(200)
                    self.send_header('Content-Type', 'image/png')
                    self.send_header('Content-Length', str(len(content)))
                    # Allow cross-origin requests for web application flexibility
                    self.send_header('Access-Control-Allow-Origin', '*') 
                    self.end_headers()
                    self.wfile.write(content)
                    return
                except IOError:
                    self.send_error(500, "Internal Server Error: System failed to read file.")
                    return
            else:
                self.send_error(404, f"Image Not Found for serial number: {serial_no}")
                return
                
        # Send a default message if someone visits the root URL (http://printmon/)
        elif self.path == '/':
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.end_headers()
            self.wfile.write(b"<h1>Printmon Image Server is Active</h1><p>Use /images/{serialno} to fetch PNG files.</p>")
            return
            
        # Fallback for any other unrecognized URL path
        self.send_error(404, "Page Not Found")

def run_server():
    # Port 80 allows you to use http://printmon without typing a port number
    server_address = ('', PORT) 
    httpd = HTTPServer(server_address, ImageServiceHandler)
    print(f"Image server running. Listening for requests at http://printmon/images/...")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping Image Server.")
        httpd.server_close()

if __name__ == '__main__':
    cameras = discover_tapo_hardware()
    for printer in PRINTER_CAMERA:
        if PRINTER_CAMERA[printer]['MAC_ADDRESS'] in cameras:
            PRINTER_CAMERA[printer]['IP_ADDRESS'] = cameras[PRINTER_CAMERA[printer]['MAC_ADDRESS']]

    print(PRINTER_CAMERA)
    run_server()

