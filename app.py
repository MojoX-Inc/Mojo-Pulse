from flask import Flask, render_template, jsonify
import psutil
import platform
import GPUtil
from threading import Thread
import time
import logging
from functools import wraps
import socket
import subprocess

app = Flask(__name__)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('mojopulse.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

class MojoPulseMetrics:
    def __init__(self):
        self.metrics = {
            'cpu': 0,
            'ram': 0,
            'disk': 0,
            'disk_total': 0,
            'disk_used': 0,
            'disk_free': 0,
            'gpu': None,
            'gpu_mem': None,
            'gpu_mem_total': None,
            'gpu_mem_free': None,
            'temp': None,
            'cpu_freq': None,
            'cpu_cores': None,
            'uptime': 0,
            'network': {
                'bytes_sent': 0,
                'bytes_recv': 0,
                'packets_sent': 0,
                'packets_recv': 0
            }
        }
        self.running = True
        self.start_time = time.time()
        self.cpu_info = self._fetch_cpu_details()
        self.gpu_info = self._fetch_gpu_details()

    def _fetch_cpu_details(self):
        cpu_details = {}
        try:
            if platform.system() == "Windows":
                cmd = "wmic cpu get caption,manufacturer /value"
                output = subprocess.check_output(cmd, shell=True, text=True).strip()
                for line in output.splitlines():
                    if line.strip():
                        key, value = line.split("=", 1)
                        if key.strip() == "Caption":
                            cpu_details['model'] = value.strip()
                        elif key.strip() == "Manufacturer":
                            cpu_details['vendor'] = value.strip()
                cpu_details['arch'] = platform.machine() or 'Unknown'
                cpu_details['bits'] = '64' if '64' in cpu_details['arch'] else '32'
            elif platform.system() == "Linux":
                cmd = "lscpu"
                output = subprocess.check_output(cmd, shell=True, text=True).strip()
                for line in output.splitlines():
                    if line.startswith("Model name:"):
                        cpu_details['model'] = line.split(":", 1)[1].strip()
                    elif line.startswith("Vendor ID:"):
                        cpu_details['vendor'] = line.split(":", 1)[1].strip()
                    elif line.startswith("Architecture:"):
                        cpu_details['arch'] = line.split(":", 1)[1].strip()
                cpu_details['bits'] = '64' if '64' in cpu_details.get('arch', '') else '32'
            else: 
                cpu_details = {
                    'model': platform.processor() or 'Unknown CPU',
                    'vendor': 'Unknown',
                    'arch': platform.machine() or 'Unknown',
                    'bits': 'N/A'
                }
            logger.debug(f"Got CPU details: {cpu_details}")
        except Exception as e:
            logger.error(f"Couldn’t grab CPU info: {e}")
            cpu_details = {'model': 'Unknown CPU', 'vendor': 'Unknown', 'arch': 'Unknown', 'bits': 'N/A'}
        return cpu_details

    def _fetch_gpu_details(self):
        try:
            gpus = GPUtil.getGPUs()
            if gpus:
                return {'name': gpus[0].name}
            return {'name': 'Unknown GPU'}
        except Exception as e:
            logger.debug(f"No GPU data: {e}")
            return {'name': 'No GPU Detected'}

    def update_metrics(self):
        while self.running:
            try:
                self.metrics['cpu'] = psutil.cpu_percent(interval=1)
                ram = psutil.virtual_memory()
                self.metrics['ram'] = ram.percent
                disk = psutil.disk_usage('/')
                self.metrics['disk'] = disk.percent
                self.metrics['disk_total'] = disk.total
                self.metrics['disk_used'] = disk.used
                self.metrics['disk_free'] = disk.free

                try:
                    gpus = GPUtil.getGPUs()
                    if gpus:
                        gpu = gpus[0]
                        self.metrics['gpu'] = gpu.load * 100
                        self.metrics['gpu_mem'] = gpu.memoryUtil * 100
                        self.metrics['gpu_mem_total'] = gpu.memoryTotal
                        self.metrics['gpu_mem_free'] = gpu.memoryFree
                except Exception as e:
                    logger.debug(f"GPU stats unavailable: {e}")
                    self.metrics['gpu'] = None
                    self.metrics['gpu_mem'] = None
                    self.metrics['gpu_mem_total'] = None
                    self.metrics['gpu_mem_free'] = None

                try:
                    temps = psutil.sensors_temperatures()
                    if 'coretemp' in temps:
                        self.metrics['temp'] = temps['coretemp'][0].current
                    elif 'cpu_thermal' in temps:
                        self.metrics['temp'] = temps['cpu_thermal'][0].current
                    else:
                        self.metrics['temp'] = None
                except Exception as e:
                    logger.debug(f"No temp data: {e}")
                    self.metrics['temp'] = None

                cpu_freq = psutil.cpu_freq()
                self.metrics['cpu_freq'] = cpu_freq.current if cpu_freq else 0
                self.metrics['cpu_cores'] = psutil.cpu_count(logical=True)

                net_io = psutil.net_io_counters()
                self.metrics['network']['bytes_sent'] = net_io.bytes_sent
                self.metrics['network']['bytes_recv'] = net_io.bytes_recv
                self.metrics['network']['packets_sent'] = net_io.packets_sent
                self.metrics['network']['packets_recv'] = net_io.packets_recv

                self.metrics['uptime'] = int(time.time() - self.start_time)

            except Exception as e:
                logger.error(f"Metrics update failed: {e}")
            time.sleep(1)

    def get_metrics(self):
        return self.metrics.copy()

    def stop(self):
        self.running = False

metrics_store = MojoPulseMetrics()

def requires_running(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not metrics_store.running:
            return jsonify({'error': 'MojoPulse is shutting down'}), 503
        return f(*args, **kwargs)
    return decorated

@app.route('/')
@requires_running
def index():
    try:
        system_info = {
            'os': platform.system(),
            'os_version': platform.version(),
            'processor': platform.processor() or 'Unknown',
            'cpu_model': metrics_store.cpu_info.get('model', 'Unknown CPU'),
            'cpu_arch': metrics_store.cpu_info.get('arch', 'Unknown'),
            'gpu_name': metrics_store.gpu_info['name'],
            'total_ram': round(psutil.virtual_memory().total / (1024**3), 2),
            'hostname': socket.gethostname()
        }
        return render_template('index.html', system_info=system_info)
    except Exception as e:
        logger.error(f"Index page error: {e}")
        return "Internal Server Error", 500

@app.route('/cpu')
@requires_running
def cpu():
    try:
        cpu_details = {
            'model': metrics_store.cpu_info.get('model', 'Unknown CPU'),
            'vendor': metrics_store.cpu_info.get('vendor', 'Unknown'),
            'arch': metrics_store.cpu_info.get('arch', 'Unknown'),
            'bits': metrics_store.cpu_info.get('bits', 'N/A')
        }
        logger.info(f"Loading CPU page with: {cpu_details}")
        return render_template('cpu.html', cpu_details=cpu_details)
    except Exception as e:
        logger.error(f"CPU page error: {e}")
        return "Internal Server Error", 500

@app.route('/gpu')
@requires_running
def gpu():
    return render_template('gpu.html', gpu_name=metrics_store.gpu_info['name'])

@app.route('/memory')
@requires_running
def memory():
    return render_template('memory.html')

@app.route('/disk')
@requires_running
def disk():
    return render_template('disk.html')

@app.route('/network')
@requires_running
def network():
    return render_template('network.html')

@app.route('/metrics')
@requires_running
def get_metrics():
    try:
        metrics = metrics_store.get_metrics()
        return jsonify(metrics)
    except Exception as e:
        logger.error(f"Metrics endpoint error: {e}")
        return jsonify({'error': 'Failed to fetch metrics'}), 500

def start_metrics_thread():
    thread = Thread(target=metrics_store.update_metrics)
    thread.daemon = True
    thread.start()
    return thread

if __name__ == '__main__':
    try:
        logger.info("MojoPulse is booting up...")
        metrics_thread = start_metrics_thread()
        app.run(debug=True, host='0.0.0.0', port=5000, threaded=True)
    except Exception as e:
        logger.error(f"MojoPulse crashed on startup: {e}")
    finally:
        metrics_store.stop()
        logger.info("MojoPulse shutting down")