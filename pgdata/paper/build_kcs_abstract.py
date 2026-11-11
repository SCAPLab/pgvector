"""KCS 2027 초록(1장) 생성: 템플릿(KCS2027_Abstract_Template_KOR.DOC)의 서식을 그대로 옮긴 .docx.

템플릿 서식 (원본 .DOC에서 추출):
  A4, 여백 좌우 2.5cm / 상하 3.5cm, 머리글 1.5cm, 바닥글 1.75cm, 1단
  제목      Arial(한글 맑은 고딕) 14pt 굵게, 가운데, 문단 뒤 0
  저자      11pt, 가운데, 소속 번호는 위 첨자
  소속      11pt 기울임, 가운데
  본문      Times New Roman(한글 맑은 고딕) 10pt, 양쪽 정렬, 줄 간격 1.08배, 문단 뒤 8pt
  그림 캡션 10pt, 가운데
  감사의 글 / 참고문헌  9pt, 머리말 단어만 굵게
실행: <python-docx가 있는 python> paper/build_kcs_abstract.py  ->  paper/KCS2027_abstract_ivf_frag.docx
"""
from pathlib import Path
from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT, WD_CELL_VERTICAL_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_COLOR_INDEX
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, Twips

ROOT = Path(__file__).resolve().parents[1]
FIG_DIR = ROOT / "plots/burst/paper"
OUT = ROOT / "paper/KCS2027_abstract_ivf_frag.docx"

# ===== 내용 =====
# 문단은 [(글자, 서식)] 목록. 서식: "b" 굵게, "i" 기울임, "sup" 위 첨자, "hl" 노란 강조(채워 넣을 자리).
TITLE = "삽입 interleaving에 의한 IVF 벡터 인덱스의 posting list 파편화와 block size 튜닝의 한계"
AUTHORS = [("OOO", ""), ("1", "sup"), (", OOO", ""), ("1", "sup"), (", OOO", ""), ("2", "sup")]
AFFIL = [("[소속 1], [소속 2]", "i hl")]

BODY = [
    # 배경·목적
    "IVF(inverted file) 인덱스[1] 기반 디스크 벡터 검색은 질의에 가까운 cluster의 posting list 전체를 SSD에서 읽으므로[2], "
    "posting list의 물리적 연속성이 검색 지연시간을 좌우한다. 그러나 빌드 이후 삽입된 벡터는 posting list의 마지막 "
    "페이지가 가득 차면 파일 끝에 새로 할당(relation extension)된 페이지에 기록되므로, 여러 posting list에 대한 삽입이 interleave되면 posting list가 "
    "비연속적으로 배치된다. 본 연구는 삽입 순서에 따른 파편화를 LBA 기준으로 정량화하고, 읽기 block size B의 튜닝으로 "
    "이를 완화할 수 있는지 분석한다.",
    # 실험 방법
    "PostgreSQL 17.11·pgvector 0.8.7[3]에서 SIFT 벡터 600만 개(128차원)로 ivfflat 인덱스(lists = 2,450)를 생성하고, "
    "hot posting list 512개에 각각 빌드 시점 크기의 50%를 집중 삽입(bursty insertion)하였다. 삽입 순서는 동시에 "
    "interleave되는 posting list 수 k = 1(순차), 16(16개 단위 무작위), 512(전체 무작위)로 달리하였다. 파편화는 "
    "extent 수(LBA가 연속인 최대 구간의 수)와 LBA 간격(같은 posting list에 연이어 할당된 두 페이지의 LBA 거리)으로 "
    "측정하였고, posting list를 포함하는 B 정렬 블록을 O_DIRECT·QD1로 읽어(B = 4KB~1MB) I/O 요청 수, "
    "read amplification, block size 튜닝 이득(B = 8KB의 읽기 시간 ÷ 최소 읽기 시간)을 측정하였다.",
    # 결과
    "삽입량이 같아도 extent 수는 k = 1에서 2.0, k = 16과 512에서 72.9였고, 후자의 LBA 간격 중앙값은 128KB와 4.4MB로 "
    "약 k × 8KB에 비례하였다(Fig 1(a)). B를 늘리면 k = 1의 I/O 요청 수는 연속 배치 수준(1MB에서 3.7회)으로 "
    "감소하였으나, k = 16은 B가 LBA 간격을 초과하는 256KB부터 감소하였고 k = 512는 extent 수 부근(74회)에서 "
    "정체되었다(Fig 1(b)). B ≤ LBA 간격이면 extent 수가 요청 수의 하한이 되어, B를 늘려도 read amplification만 "
    "증가한다(k = 512, 1MB에서 43.8; Fig 1(c)). 그 결과 block size 튜닝 이득의 중앙값은 k = 1에서 2.89배로 "
    "삽입 전(2.79배)과 유사하였으나, k = 16과 512에서는 1.38배, 1.10배로 감소하였다(Fig 1(d)).",
    # 결론
    "즉 삽입 순서가 파편화의 정도와 형태를 결정하며, interleave된 삽입에서는 block size 튜닝만으로 읽기 성능을 "
    "회복할 수 없으므로 인덱스 재빌드 또는 posting list의 물리 배치를 변경하는 기법이 필요하다.",
]

# 그림: (파일, 하위 라벨). 가로 한 줄에 같은 높이로 놓는다.
FIGS = [
    ("fig2_extent_vs_gap.png", "(a)"),
    ("fig3_requests_vs_B.png", "(b)"),
    ("fig4_read_amp_vs_B.png", "(c)"),
    ("fig6_tuning_gain.png", "(d)"),
]
FIG_HEIGHT_CM = 3.0
CAPTION = ("Fig 1. (a) Number of extents vs. LBA gap per hot posting list. (b) I/O requests and (c) read amplification "
           "vs. block size B (mean of 512 hot posting lists). (d) Block size tuning gain (median, p10–p90).")
ACK = [("Acknowledgments", "b"), (" ", ""), ("[과제 정보]", "hl")]
REFS = [("References", "b"), (" [1] H. Jégou et al., IEEE Trans. Pattern Anal. Mach. Intell. 33, 117 (2011). "
        "[2] Q. Chen et al., Adv. Neural Inf. Process. Syst. 34 (2021). "
        "[3] pgvector, https://github.com/pgvector/pgvector.", "")]
# =================


def set_fonts(rpr_owner, ascii_font, east_asia="맑은 고딕"):
    rpr = rpr_owner.get_or_add_rPr()
    rfonts = rpr.find(qn("w:rFonts"))
    if rfonts is None:
        rfonts = OxmlElement("w:rFonts")
        rpr.insert(0, rfonts)
    for k in ("w:ascii", "w:hAnsi", "w:cs"):
        rfonts.set(qn(k), ascii_font)
    rfonts.set(qn("w:eastAsia"), east_asia)
    for k in ("w:asciiTheme", "w:hAnsiTheme", "w:eastAsiaTheme", "w:cstheme"):
        rfonts.attrib.pop(qn(k), None)


def para(doc_or_cell, runs, size=None, align=None, after=None, font=None):
    p = doc_or_cell.add_paragraph()
    if align is not None:
        p.alignment = align
    if after is not None:
        p.paragraph_format.space_after = Pt(after)
    for text, fmt in runs:
        r = p.add_run(text)
        if size:
            r.font.size = Pt(size)
        r.bold = "b" in fmt.split() or None
        r.italic = "i" in fmt.split() or None
        if "sup" in fmt.split():
            r.font.superscript = True
        if "hl" in fmt.split():
            r.font.highlight_color = WD_COLOR_INDEX.YELLOW
        if font:
            set_fonts(r._r, font)
    return p


TBLPR_ORDER = ["tblStyle", "tblpPr", "tblOverlap", "bidiVisual", "tblStyleRowBandSize", "tblStyleColBandSize", "tblW",
               "jc", "tblCellSpacing", "tblInd", "tblBorders", "shd", "tblLayout", "tblCellMar", "tblLook"]


def insert_tblpr(tblPr, el):
    """tblPr 자식은 스키마 순서를 지켜야 Word가 연다."""
    rank = TBLPR_ORDER.index(el.tag.split("}")[1])
    for child in tblPr:
        name = child.tag.split("}")[1]
        if name in TBLPR_ORDER and TBLPR_ORDER.index(name) > rank:
            child.addprevious(el)
            return
    tblPr.append(el)


def no_borders(table):
    tblPr = table._tbl.tblPr
    borders = OxmlElement("w:tblBorders")
    for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
        e = OxmlElement(f"w:{edge}")
        e.set(qn("w:val"), "nil")
        borders.append(e)
    insert_tblpr(tblPr, borders)
    mar = OxmlElement("w:tblCellMar")
    for edge, v in (("top", 0), ("left", 28), ("bottom", 0), ("right", 28)):
        e = OxmlElement(f"w:{edge}")
        e.set(qn("w:w"), str(v))
        e.set(qn("w:type"), "dxa")
        mar.append(e)
    insert_tblpr(tblPr, mar)


def build():
    doc = Document()
    zoom = doc.settings.element.find(qn("w:zoom"))
    if zoom is not None:
        zoom.set(qn("w:percent"), "100")
    sec = doc.sections[0]
    sec.page_width, sec.page_height = Twips(11906), Twips(16838)
    sec.left_margin = sec.right_margin = Twips(1418)
    sec.top_margin = sec.bottom_margin = Twips(1985)
    sec.header_distance, sec.footer_distance = Twips(851), Twips(992)

    normal = doc.styles["Normal"]
    normal.font.size = Pt(10)
    set_fonts(normal.element, "Times New Roman")
    lang = OxmlElement("w:lang")
    lang.set(qn("w:val"), "en-US")
    lang.set(qn("w:eastAsia"), "ko-KR")
    normal.element.get_or_add_rPr().append(lang)
    pf = normal.paragraph_format
    pf.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
    pf.line_spacing = 259 / 240
    pf.space_before, pf.space_after = Pt(0), Pt(8)

    C = WD_ALIGN_PARAGRAPH.CENTER
    para(doc, [(TITLE, "b")], size=14, align=C, after=0, font="Arial")
    para(doc, [("", "b")], size=14, align=C, after=0)
    para(doc, AUTHORS, size=11, align=C, after=0)
    para(doc, AFFIL, size=11, align=C)
    for text in BODY:
        para(doc, [(text, "")])

    # 그림 4개: 1행 = 그림, 2행 = (a)~(d). 열 폭은 같은 높이일 때의 그림 폭에 맞춘다.
    from PIL import Image
    widths = []
    for name, _ in FIGS:
        w, h = Image.open(FIG_DIR / name).size
        widths.append(FIG_HEIGHT_CM * w / h)
    table = doc.add_table(rows=2, cols=len(FIGS))
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table.autofit = False
    no_borders(table)
    for j, ((name, label), w) in enumerate(zip(FIGS, widths)):
        col_w = Cm(w + 0.12)
        for i in range(2):
            cell = table.cell(i, j)
            cell.width = col_w
            cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.BOTTOM
            p = cell.paragraphs[0]
            p.alignment = C
            p.paragraph_format.space_after = Pt(0)
            p.paragraph_format.line_spacing = 1.0
            if i == 0:
                p.add_run().add_picture(str(FIG_DIR / name), height=Cm(FIG_HEIGHT_CM))
            else:
                r = p.add_run(label)
                r.font.size = Pt(9)
    para(doc, [(CAPTION, "")], align=C, after=8).paragraph_format.space_before = Pt(4)
    para(doc, ACK, size=9, align=WD_ALIGN_PARAGRAPH.LEFT)
    para(doc, REFS, size=9)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    doc.save(OUT)
    print("saved", OUT, "| fig widths cm:", [round(w, 2) for w in widths], "total", round(sum(widths), 2))


if __name__ == "__main__":
    build()
