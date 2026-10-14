import sys
from pathlib import Path
sys.path.append(str(Path("d:/project_a/backend").resolve()))
from ppt_agent import ppt_agent

info = ppt_agent.inspect_ppt(r"D:\everthing\HIREZAP_Pitch_Deck.pptx")
print("Presentation:", info["name"])
print("Total Slides:", info["total_slides"])
for s in info["slides"]:
    print(f"Slide {s['slide_num']}: {s['title']} | {s['preview_text']}")
