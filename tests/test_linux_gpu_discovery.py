import tempfile
import unittest
from pathlib import Path
from core.device_agent_client import _discover_linux_gpus

class LinuxGPUDiscoveryTests(unittest.TestCase):
 def test_reads_amd_vram_from_sysfs_without_external_commands(self):
  with tempfile.TemporaryDirectory() as tmp:
   device=Path(tmp)/"card0"/"device"; device.mkdir(parents=True)
   (device/"vendor").write_text("0x1002")
   (device/"product_name").write_text("AMD Test Accelerator")
   (device/"mem_info_vram_total").write_text(str(16*1024**3))
   (device/"mem_info_vram_used").write_text(str(4*1024**3))
   result=_discover_linux_gpus(tmp)
  self.assertEqual(len(result),1)
  self.assertEqual(result[0]["vendor"],"AMD")
  self.assertEqual(result[0]["name"],"AMD Test Accelerator")
  self.assertEqual(result[0]["memory_available_bytes"],12*1024**3)
 def test_ignores_non_amd_and_invalid_devices(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp)
   for index,vendor in enumerate(("0x10de","0x1002")):
    device=root/f"card{index}"/"device"; device.mkdir(parents=True)
    (device/"vendor").write_text(vendor)
    (device/"mem_info_vram_total").write_text("bad")
    (device/"mem_info_vram_used").write_text("0")
   self.assertEqual(_discover_linux_gpus(root),[])
if __name__=="__main__": unittest.main()
