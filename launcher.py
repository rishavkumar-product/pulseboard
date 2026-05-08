import sys, os
os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import uvicorn
uvicorn.run("main:app", host="0.0.0.0", port=8010)
