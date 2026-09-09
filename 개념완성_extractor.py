"""개념완성 PDF를 페이지 유형에 맞춰 문제별 PNG로 추출한다."""
from __future__ import annotations
import argparse, re
from dataclasses import dataclass
from pathlib import Path
import pdfplumber
import pypdfium2 as pdfium
from PIL import Image, ImageDraw
from pungsanja_extractor import (
    CAPTURE_TOP_PADDING,
    add_margin,
    is_colored_text,
    pdf_box_to_pixels,
)

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

def decoration_color(value):
    if not isinstance(value,(tuple,list)): return False
    try: values=tuple(float(v) for v in value)
    except (TypeError,ValueError): return False
    if len(values)==1: return values[0]>.35
    if len(values)==4 and max(values[:3])<.04:
        # CMYK의 K 채널만 쓰는 옅은 회색 장식선. 검정 수식선(K=1)은 제외한다.
        return .03<values[3]<.75
    if len(values)==3 and max(values)-min(values)<.04:
        return .2<sum(values)/3<.96
    return is_colored_text(values)

def graphic_is_decorative_color(obj):
    # PDF 제작 방식에 따라 선은 stroking_color, 얇은 사각형은
    # non_stroking_color에 실제 색이 들어간다. 둘 중 하나라도 장식색이면 된다.
    return any(decoration_color(obj.get(key)) for key in ("stroking_color","non_stroking_color","fill"))

def view_labels(page):
    return [w for w in words(page) if re.sub(r"[<>\[\]〈〉]","",norm(w["text"]))=="보기"]

def is_view_frame(page,obj):
    if not all(k in obj for k in ("x0","x1","top","bottom")): return False
    x0,y0,x1,y1=map(float,(obj["x0"],obj["top"],obj["x1"],obj["bottom"]))
    if x1-x0<page.width*.3 or not 30<y1-y0<180: return False
    return any(x0-4<=float(w["x0"])<=x0+55 and y0-12<=float(w["top"])<=y0+12 for w in view_labels(page))

def decorative_graphic(page,obj,kind,left,right):
    x0,y0,x1,y1=map(float,(obj["x0"],obj["top"],obj["x1"],obj["bottom"]))
    width=x1-x0; height=y1-y0; column_width=right-left
    spans_columns=x0<left+8 and x1>right-8 and width>column_width*1.15
    long_colored_rule=(kind in {"curve","rect","line"} and width>column_width*.42 and height<36 and graphic_is_decorative_color(obj))
    return spans_columns or long_colored_rule or is_view_frame(page,obj)

def remove_concept_decorative_lines(page,image):
    """개념완성의 긴 색상/회색 장식선만 지우고 수식·도형 선은 보존한다."""
    result=image.copy(); draw=ImageDraw.Draw(result); curve_minimum=page.width*.035; flat_minimum=page.width*.12
    capsules=[o for o in page.curves if float(o.get("x1",0))-float(o.get("x0",0))<page.width*.12 and float(o.get("bottom",0))-float(o.get("top",0))<40 and decoration_color(o.get("non_stroking_color"))]
    for obj in page.curves:
        # 색상 채움만 있는 번호 캡슐은 보존하고, 실제 색상 선만 지운다.
        if not decoration_color(obj.get("stroking_color")): continue
        current=None
        for command in obj.get("path",()):
            op=command[0]; endpoint=command[-1] if len(command)>1 else None
            if not isinstance(endpoint,(tuple,list)) or len(endpoint)<2: continue
            point=(float(endpoint[0]),float(endpoint[1]))
            if op=="l" and current is not None and abs(point[1]-current[1])<1.2 and abs(point[0]-current[0])>=curve_minimum:
                x0,x1=sorted((current[0],point[0])); y=(current[1]+point[1])/2
                # 둥근 끝부분도 남지 않도록 수평 구간의 양옆을 조금 넓혀 지운다.
                px=pdf_box_to_pixels(page,result,(x0-16,y-2.5,x1+16,y+2.5))
                if px[2]>=px[0] and px[3]>=px[1]: draw.rectangle(px,fill="white")
            current=point
    for obj in (*page.lines,*page.rects):
        if not all(k in obj for k in ("x0","x1","top","bottom")): continue
        x0,y0,x1,y1=map(float,(obj["x0"],obj["top"],obj["x1"],obj["bottom"]))
        if x1-x0>=flat_minimum and y1-y0<=4 and graphic_is_decorative_color(obj):
            # 장식선이 번호 캡슐 밑으로 들어가는 디자인은 캡슐 밖 부분만 지운다.
            for cap in capsules:
                cx0,cy0,cx1,cy1=map(float,(cap["x0"],cap["top"],cap["x1"],cap["bottom"]))
                if cy1>=y0-2 and cy0<=y1+2:
                    if cx0-1<=x0<=cx1<x1: x0=cx1+1
                    elif x0<cx0<=x1<=cx1+1: x1=cx0-1
            px=pdf_box_to_pixels(page,result,(x0-2,y0-2.5,x1+2,y1+2.5))
            if px[2]>=px[0] and px[3]>=px[1]: draw.rectangle(px,fill="white")
    return result

def remove_view_box_decorations(page,image):
    """<보기>의 문항 내용은 남기고 라벨과 외곽 테두리만 지운다."""
    result=image.copy(); draw=ImageDraw.Draw(result); labels=view_labels(page)
    if not labels: return result
    for obj in page.curves:
        if not is_view_frame(page,obj): continue
        x0,y0,x1,y1=map(float,(obj["x0"],obj["top"],obj["x1"],obj["bottom"]))
        for box in ((x0-3,y0-3,x1+3,y0+3),(x0-3,y1-3,x1+3,y1+3),(x0-3,y0-3,x0+3,y1+3),(x1-3,y0-3,x1+3,y1+3)):
            px=pdf_box_to_pixels(page,result,box)
            if px[2]>=px[0] and px[3]>=px[1]: draw.rectangle(px,fill="white")
        for label in labels:
            if x0-4<=float(label["x0"])<=x0+55 and y0-12<=float(label["top"])<=y0+12:
                label_box=(float(label["x0"])-10,float(label["top"])-6,float(label["x1"])+10,float(label["bottom"])+6)
                draw.rectangle(pdf_box_to_pixels(page,result,label_box),fill="white")
    return result

def tight(page,left,right,top,bottom):
    objs=[]
    for c in page.chars:
        cx=(float(c["x0"])+float(c["x1"]))/2; cy=(float(c["top"])+float(c["bottom"]))/2
        if left<=cx<right and top<=cy<bottom: objs.append((float(c["x0"]),float(c["top"]),float(c["x1"]),float(c["bottom"])))
    for kind,coll in (("rect",page.rects),("curve",page.curves),("image",page.images),("line",page.lines)):
        for o in coll:
            if not all(k in o for k in ("x0","x1","top","bottom")): continue
            x0,y0,x1,y1=map(float,(o["x0"],o["top"],o["x1"],o["bottom"]))
            if decorative_graphic(page,o,kind,left,right): continue
            if y1-y0>(bottom-top)*.85 and x1-x0<2: continue
            if x1-x0>(right-left)*.92 and y1-y0<3: continue
            # 중심점이 아니라 실제 외곽 상자의 교차 여부로 판정해, 아래로
            # 길게 내려오는 도형과 분할된 곡선의 끝부분을 보존한다.
            if x1>left and x0<right and y1>top and y0<bottom: objs.append((x0,y0,x1,y1))
    if not objs: return None
    # 열 경계 바로 옆에서 시작하는 문제도 동일한 7pt 왼쪽 여백을 갖게 한다.
    # 객체 선택은 이미 left/right 안에서 끝났으므로, 상자만 조금 확장해도
    # 옆 열의 문제 내용이 섞이지 않는다.
    return (max(0,min(x[0] for x in objs)-7),max(0,min(x[1] for x in objs)-4),min(right,max(x[2] for x in objs)+7),min(page.height,max(x[3] for x in objs)+4))

def region_image(page,image,box,target_width=None):
    if box is None: return None
    cropped=image.crop(pdf_box_to_pixels(page,image,box)).convert("RGB")
    if target_width is None: return add_margin(cropped,24)
    crop_x0,_,crop_x1,_=page.cropbox or page.mediabox
    pixels_per_point=image.width/(crop_x1-crop_x0)
    canvas_width=max(cropped.width,round(target_width*pixels_per_point))
    result=Image.new("RGB",(canvas_width+48,cropped.height+48),"white")
    result.paste(cropped,(24,24)); return result

def save(page,image,box,out,name,target_width=None):
    result=region_image(page,image,box,target_width)
    if result is None: return None
    path=out/name; result.save(path,"PNG",optimize=True); return path

def stack(a,b):
    out=Image.new("RGB",(max(a.width,b.width),a.height+b.height+18),"white"); out.paste(a,(0,0)); out.paste(b,(0,a.height+18)); return out

def labels(page,label): return [w for w in words(page) if norm(w["text"]).startswith(label)]

def examples(page,image,out,pno):
    ws=words(page); labs=[(w,k) for w in ws for k in ("예제","유제") if norm(w["text"]).startswith(k)]; labs.sort(key=lambda z:(float(z[0]["top"]),float(z[0]["x0"])))
    result=[]; mid=page.width/2
    for serial,(w,kind) in enumerate(labs,1):
        x=float(w["x0"]); top=float(w["top"]); col=int(x>=mid); left=55 if not col else mid+5; right=mid-5 if not col else page.width-55
        end=min([float(q["top"]) for q,_ in labs if int(float(q["x0"])>=mid)==col and float(q["top"])>top+5] or [page.height-90])
        # 좌우 예제 사이에 다음 '개념' 설명 상자가 끼는 구성에서는
        # 다음 예제 번호보다 개념 상자의 시작을 먼저 경계로 사용한다.
        concept_starts=[float(q["top"]) for q in ws if norm(q["text"])=="개념" and top<float(q["top"])<end]
        if concept_starts: end=min(concept_starts)-8
        stops=[float(q["top"]) for q in ws if left<=float(q["x0"])<right and top<float(q["top"])<end and (norm(q["text"]).startswith("풀이") or norm(q["text"])=="답")]
        if stops:
            # '풀이' 캡슐 배경은 글자보다 조금 위에서 시작하므로 충분히 앞에서 끊는다.
            end=min(stops)-10
        box=tight(page,left,right,top-CAPTURE_TOP_PADDING,end)
        # 도형이 풀이 행보다 아래까지 내려오는 예제는 도형을 살리되 왼쪽 풀이 글자만 지운다.
        if kind=="예제" and stops and box is not None and box[3]>end:
            cleaned=image.copy()
            # PDF마다 CropBox 원점이 달라질 수 있다. 자르기와 같은 좌표 변환을
            # 사용하지 않으면 풀이 삭제 마스크가 위로 이동해 발문을 가린다.
            mask_box=pdf_box_to_pixels(
                page,
                image,
                (left, end, left+(right-left)*.58, box[3]),
            )
            ImageDraw.Draw(cleaned).rectangle(mask_box,fill="white")
            path=save(page,cleaned,box,out,f"{pno:03d}p_{serial:03d}_{kind}.png",right-left)
        else:
            path=save(page,image,box,out,f"{pno:03d}p_{serial:03d}_{kind}.png",right-left)
        if path: result.append(path)
    return result

def numbered(page,image,out,pno):
    ms=markers(page); result=[]; d=divider(page); two=any(m.column for m in ms); serial=0
    for col in ((0,1) if two else (0,)):
        cms=[m for m in ms if m.column==col]; left=55 if not col else (d or page.width/2)+5
        right=(d-5 if d and not col else page.width/2-5 if two and not col else page.width-55)
        for i,m in enumerate(cms):
            end=cms[i+1].top-5 if i+1<len(cms) else page.height-90; serial+=1
            box=tight(page,left,right,m.top-CAPTURE_TOP_PADDING,end)
            if box is not None: box=(max(box[0],m.x0-7),box[1],box[2],box[3])
            path=save(page,image,box,out,f"{pno:03d}p_{serial:03d}.png",right-left)
            if path: result.append(path)
    return result

def types(page,image,out,pno):
    ws=words(page); d=divider(page) or page.width/2
    heads=[w for w in ws if float(w["x0"])<d and norm(w["text"]).startswith("유형") and any(c.isdigit() for c in str(w["text"]))]; heads.sort(key=lambda w:float(w["top"]))
    rms=[m for m in markers(page) if m.x0>d]; result=[]; serial=0
    for i,w in enumerate(heads):
        end=float(heads[i+1]["top"])-8 if i+1<len(heads) else page.height-90; serial+=1
        path=save(page,image,tight(page,55,d-5,float(w["top"])-CAPTURE_TOP_PADDING,end),out,f"{pno:03d}p_{serial:03d}_유형.png",d-60)
        if path: result.append(path)
    for i,m in enumerate(rms):
        end=rms[i+1].top-5 if i+1<len(rms) else page.height-90; serial+=1
        path=save(page,image,tight(page,d+5,page.width-55,m.top-CAPTURE_TOP_PADDING,end),out,f"{pno:03d}p_{serial:03d}_비슷한문제.png",page.width-60-d)
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
                stop=min([float(w["top"]) for w in stops] or [end]); box=tight(page,left,right,m.top-CAPTURE_TOP_PADDING,stop-2)
                if box is not None: box=(box[0],box[1],box[2],min(box[3],stop-2))
                path=save(page,image,box,out,f"{pno:03d}p_{serial:03d}.png",right-left)
                if path: result.append(path)
                continue
            first=float(stages[0]["top"]); header_end=min([float(w["top"]) for w in stops if float(w["top"])<first] or [first])-3; hb=tight(page,left,right,m.top-CAPTURE_TOP_PADDING,header_end)
            if hb is None: continue
            header=region_image(page,image,hb,right-left)
            for si,stage in enumerate(stages):
                top=float(stage["top"])-3; stop=float(stages[si+1]["top"])-5 if si+1<len(stages) else end
                marks=[float(w["top"]) for w in stops if top<float(w["top"])<stop]
                if marks: stop=min(marks)-10
                sb=tight(page,left,right,top,stop)
                if sb is None: continue
                # 답 캡슐의 둥근 외곽선이 경계를 살짝 침범해도 결과에는 포함하지 않는다.
                sb=(sb[0],sb[1],sb[2],min(sb[3],stop))
                stage_img=region_image(page,image,sb,right-left); path=out/f"{pno:03d}p_{serial:03d}_{si+1}단계.png"; stack(header,stage_img).save(path,"PNG",optimize=True); result.append(path)
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
                image=renderer[i].render(scale=scale).to_pil().convert("RGB")
                # 공용 장식선 제거기는 개념완성의 번호 캡슐 아래로 들어간 선까지
                # 통째로 지울 수 있어, 이 책 전용 마스킹만 적용한다.
                image=remove_concept_decorative_lines(page,image)
                image=remove_view_box_decorations(page,image)
                fn={"examples":examples,"types":types,"written":written,"numbered":numbered}[kind]; made=fn(page,image,output,i+1); result.extend(made); print(f"page {i+1}: {kind}, {len(made)} image(s)")
    finally: renderer.close()
    return sorted(result)

if __name__=="__main__":
    p=argparse.ArgumentParser(description=__doc__); p.add_argument("pdf",type=Path); p.add_argument("-o","--output",type=Path,default=Path("output/개념완성")); p.add_argument("--scale",type=float,default=3.0); a=p.parse_args(); r=extract(a.pdf,a.output,a.scale); print(f"done: {len(r)} image(s)")
