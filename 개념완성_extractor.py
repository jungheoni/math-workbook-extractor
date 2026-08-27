"""개념완성 PDF를 페이지 유형에 맞춰 문제별 PNG로 추출한다."""
from __future__ import annotations
import argparse, re
from dataclasses import dataclass
from pathlib import Path
import pdfplumber
import pypdfium2 as pdfium
from PIL import Image, ImageDraw
from pungsanja_extractor import add_margin, is_colored_text, pdf_box_to_pixels

@dataclass(frozen=True)
class Marker:
    number: str; x0: float; top: float; x1: float; bottom: float; column: int = 0

def norm(text): return re.sub(r"[\s◦·]", "", str(text))
def words(page): return page.extract_words(use_text_flow=False, x_tolerance=2, y_tolerance=2)

def markers(page):
    found=[]
    for w in page.extract_words(use_text_flow=True,x_tolerance=2,y_tolerance=2,extra_attrs=["non_stroking_color","size"]):
        t=str(w["text"]).strip(); size=float(w.get("size",0))
        if re.fullmatch(r"\d{1,2}",t) and 14<=size<=18 and is_colored_text(w.get("non_stroking_color")):
            found.append(Marker(t,float(w["x0"]),float(w["top"]),float(w["x1"]),float(w["bottom"]),int(float(w["x0"])>=page.width/2)))
    return sorted(found,key=lambda m:(m.column,m.top,m.x0))

def divider(page):
    lines=[x for x in page.lines if float(x.get("height",0))>page.height*.35 and abs(float(x["x1"])-float(x["x0"]))<2 and page.width*.4<float(x["x0"])<page.width*.75]
    return float(lines[0]["x0"]) if lines else None

def tight(page,left,right,top,bottom):
    objs=[]
    for c in page.chars:
        cx=(float(c["x0"])+float(c["x1"]))/2; cy=(float(c["top"])+float(c["bottom"]))/2
        if left<=cx<right and top<=cy<bottom: objs.append((float(c["x0"]),float(c["top"]),float(c["x1"]),float(c["bottom"])))
    for coll in (page.rects,page.curves,page.images,page.lines):
        for o in coll:
            if not all(k in o for k in ("x0","x1","top","bottom")): continue
            x0,y0,x1,y1=map(float,(o["x0"],o["top"],o["x1"],o["bottom"]))
            if y1-y0>(bottom-top)*.85 and x1-x0<2: continue
            if x1-x0>(right-left)*.92 and y1-y0<3: continue
            # 중심점이 아니라 실제 외곽 상자의 교차 여부로 판정해, 아래로
            # 길게 내려오는 도형과 분할된 곡선의 끝부분을 보존한다.
            if x1>left and x0<right and y1>top and y0<bottom: objs.append((x0,y0,x1,y1))
    if not objs: return None
    return (max(left,min(x[0] for x in objs)-7),max(0,min(x[1] for x in objs)-4),min(right,max(x[2] for x in objs)+7),min(page.height,max(x[3] for x in objs)+4))

def save(page,image,box,out,name):
    if box is None: return None
    result=add_margin(image.crop(pdf_box_to_pixels(page,image,box)),24); path=out/name; result.save(path,"PNG",optimize=True); return path

def stack(a,b):
    out=Image.new("RGB",(max(a.width,b.width),a.height+b.height+18),"white"); out.paste(a,(0,0)); out.paste(b,(0,a.height+18)); return out

def labels(page,label): return [w for w in words(page) if norm(w["text"]).startswith(label)]

def examples(page,image,out,pno):
    ws=words(page); labs=[(w,k) for w in ws for k in ("예제","유제") if norm(w["text"]).startswith(k)]; labs.sort(key=lambda z:(float(z[0]["top"]),float(z[0]["x0"])))
    result=[]; mid=page.width/2
    for serial,(w,kind) in enumerate(labs,1):
        x=float(w["x0"]); top=float(w["top"]); col=int(x>=mid); left=55 if not col else mid+5; right=mid-5 if not col else page.width-55
        end=min([float(q["top"]) for q,_ in labs if int(float(q["x0"])>=mid)==col and float(q["top"])>top+5] or [page.height-90])
        if kind=="예제":
            stops=[float(q["top"]) for q in ws if left<=float(q["x0"])<right and top<float(q["top"])<end and norm(q["text"]).startswith("풀이")]
            # '풀이' 캡슐 배경은 글자보다 조금 위에서 시작하므로 충분히 앞에서 끊는다.
            if stops: end=min(stops)-10
        box=tight(page,left,right,top-3,end)
        # 도형이 풀이 행보다 아래까지 내려오는 예제는 도형을 살리되 왼쪽 풀이 글자만 지운다.
        if kind=="예제" and stops and box is not None and box[3]>end:
            cleaned=image.copy(); sx=image.width/page.width; sy=image.height/page.height
            ImageDraw.Draw(cleaned).rectangle((int(left*sx),int(end*sy),int((left+(right-left)*.58)*sx),int(box[3]*sy)+2),fill="white")
            path=save(page,cleaned,box,out,f"{pno:03d}p_{serial:03d}_{kind}.png")
        else:
            path=save(page,image,box,out,f"{pno:03d}p_{serial:03d}_{kind}.png")
        if path: result.append(path)
    return result

def numbered(page,image,out,pno):
    ms=markers(page); result=[]; d=divider(page); two=any(m.column for m in ms); serial=0
    for col in ((0,1) if two else (0,)):
        cms=[m for m in ms if m.column==col]; left=55 if not col else (d or page.width/2)+5
        right=(d-5 if d and not col else page.width/2-5 if two and not col else page.width-55)
        for i,m in enumerate(cms):
            end=cms[i+1].top-5 if i+1<len(cms) else page.height-90; serial+=1
            path=save(page,image,tight(page,left,right,m.top-3,end),out,f"{pno:03d}p_{serial:03d}.png")
            if path: result.append(path)
    return result

def types(page,image,out,pno):
    ws=words(page); d=divider(page) or page.width/2
    heads=[w for w in ws if float(w["x0"])<d and norm(w["text"]).startswith("유형") and any(c.isdigit() for c in str(w["text"]))]; heads.sort(key=lambda w:float(w["top"]))
    rms=[m for m in markers(page) if m.x0>d]; result=[]; serial=0
    for i,w in enumerate(heads):
        end=float(heads[i+1]["top"])-8 if i+1<len(heads) else page.height-90; serial+=1
        path=save(page,image,tight(page,55,d-5,float(w["top"])-4,end),out,f"{pno:03d}p_{serial:03d}_유형.png")
        if path: result.append(path)
    for i,m in enumerate(rms):
        end=rms[i+1].top-5 if i+1<len(rms) else page.height-90; serial+=1
        path=save(page,image,tight(page,d+5,page.width-55,m.top-4,end),out,f"{pno:03d}p_{serial:03d}_비슷한문제.png")
        if path: result.append(path)
    return result

def written(page,image,out,pno):
    ws=words(page); ms=markers(page); d=divider(page) or page.width/2; result=[]; serial=0
    for col in (0,1):
        cms=[m for m in ms if m.column==col]; left=55 if not col else d+5; right=d-5 if not col else page.width-55
        for i,m in enumerate(cms):
            end=cms[i+1].top-5 if i+1<len(cms) else page.height-90
            region=[w for w in ws if left<=float(w["x0"])<right and m.top<float(w["top"])<end]
            stages=sorted([w for w in region if re.search(r"\[?\d+단계\]?",norm(w["text"]))],key=lambda w:float(w["top"]))
            stops=[w for w in region if norm(w["text"]).startswith(("풀이","답"))]; serial+=1
            if not stages:
                stop=min([float(w["top"]) for w in stops] or [end]); path=save(page,image,tight(page,left,right,m.top-3,stop-2),out,f"{pno:03d}p_{serial:03d}.png")
                if path: result.append(path)
                continue
            first=float(stages[0]["top"]); header_end=min([float(w["top"]) for w in stops if float(w["top"])<first] or [first])-3; hb=tight(page,left,right,m.top-3,header_end)
            if hb is None: continue
            header=add_margin(image.crop(pdf_box_to_pixels(page,image,hb)),24)
            for si,stage in enumerate(stages):
                top=float(stage["top"])-3; stop=float(stages[si+1]["top"])-5 if si+1<len(stages) else end
                marks=[float(w["top"]) for w in stops if top<float(w["top"])<stop]
                if marks: stop=min(marks)-2
                sb=tight(page,left,right,top,stop)
                if sb is None: continue
                stage_img=add_margin(image.crop(pdf_box_to_pixels(page,image,sb)),24); path=out/f"{pno:03d}p_{serial:03d}_{si+1}단계.png"; stack(header,stage_img).save(path,"PNG",optimize=True); result.append(path)
    return result

def classify(page):
    text=norm(page.extract_text() or "")
    if "서술형꽉잡기" in text: return "written"
    type_labels=labels(page,"유형")
    if ("유형확인하기" in text or type_labels) and any(any(c.isdigit() for c in str(w["text"])) for w in type_labels): return "types"
    if labels(page,"예제") or labels(page,"유제"): return "examples"
    if markers(page): return "numbered"
    return "skip"

def extract(source:Path,output:Path,scale:float=3.0):
    output.mkdir(parents=True,exist_ok=True); renderer=pdfium.PdfDocument(str(source)); result=[]
    try:
        with pdfplumber.open(source) as doc:
            for i,page in enumerate(doc.pages):
                kind=classify(page)
                if kind=="skip": continue
                image=renderer[i].render(scale=scale).to_pil().convert("RGB"); fn={"examples":examples,"types":types,"written":written,"numbered":numbered}[kind]; made=fn(page,image,output,i+1); result.extend(made); print(f"page {i+1}: {kind}, {len(made)} image(s)")
    finally: renderer.close()
    return sorted(result)

if __name__=="__main__":
    p=argparse.ArgumentParser(description=__doc__); p.add_argument("pdf",type=Path); p.add_argument("-o","--output",type=Path,default=Path("output/개념완성")); p.add_argument("--scale",type=float,default=3.0); a=p.parse_args(); r=extract(a.pdf,a.output,a.scale); print(f"done: {len(r)} image(s)")
