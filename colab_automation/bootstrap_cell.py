# Paste this into one Colab cell and run it. Leave the cell running.
from google.colab import drive, auth
drive.mount('/content/drive')
auth.authenticate_user()
import glob
exec(open(sorted(glob.glob('/content/drive/MyDrive/Colab_Automation/worker_v*.py'))[-1]).read())
