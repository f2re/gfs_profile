from __future__ import annotations

import tempfile
from dataclasses import dataclass
from pathlib import Path

from cloudgram_product import CloudgramCell, CloudgramData
from plot_style import (
    METEO,
    PRECIP_TYPE_COLORS,
    add_footer,
    apply_meteo_rcparams,
    cb_cmap_and_norm,
    ceiling_cmap_and_norm,
    cloud_cover_cmap_and_norm,
    precip_cmap_and_norm,
    style_axis,
    value_text_color,
)

@dataclass(frozen=True)
class CloudgramRow:
    key: str
    label: str
    unit: str

CLOUDGRAM_ROWS = (
    CloudgramRow("high", "Высокая", "%"),
    CloudgramRow("mid", "Средняя", "%"),
    CloudgramRow("low", "Низкая", "%"),
    CloudgramRow("total", "Общая", "%"),
    CloudgramRow("precip", "Осадки", "мм"),
    CloudgramRow("ptype", "Тип", "код"),
    CloudgramRow("cb", "Гроза", "0–3"),
    CloudgramRow("ceiling", "ВНГО", "м"),
    CloudgramRow("vis", "Видимость", "км"),
    CloudgramRow("phen", "Явления", ""),
)


def _cloud_value(cell: CloudgramCell, key: str):
    return getattr(cell, f"{key}_cloud_pct", None) if key in {"high","mid","low","total"} else None


def _cell(row: CloudgramRow, cell: CloudgramCell):
    if row.key in {"high","mid","low","total"}:
        v=_cloud_value(cell,row.key)
        cmap,norm=cloud_cover_cmap_and_norm()
        if v is None:
            return "#E6EBF1","—",METEO.axis_text
        return cmap(norm(v)),f"{v:.0f}",value_text_color(v,"cloud")

    if row.key=="precip":
        v=cell.precip_mm
        cmap,norm=precip_cmap_and_norm()
        if v is None:
            return "#E6EBF1","—",METEO.axis_text
        return cmap(norm(v)),f"{v:.1f}",value_text_color(v,"precip")

    if row.key=="ptype":
        t=cell.precip_type or "—"
        b=t.split("/",1)[0]
        return PRECIP_TYPE_COLORS.get(b,"#F3F4F6"),t,METEO.axis_text

    if row.key=="cb":
        cmap,norm=cb_cmap_and_norm()
        v=float(cell.cb_score)
        return cmap(norm(v)),str(int(v)),value_text_color(v,"cb")

    if row.key=="ceiling":
        v=cell.ceiling_m
        cmap,norm=ceiling_cmap_and_norm()
        if v is None:
            return "#E6EBF1","—",METEO.axis_text
        txt=f"{v/1000:.1f}к" if v>=1000 else str(int(v))
        return cmap(norm(v)),txt,value_text_color(v,"ceiling")

    if row.key=="vis":
        v=cell.visibility_km
        if v is None:
            return "#E6EBF1","—",METEO.axis_text
        shade=max(0.2,min(1.0,v/10.0))
        return (1-shade,1-shade,1),f"{v:.0f}",METEO.axis_text

    if row.key=="phen":
        return "#FFFFFF",cell.phenomena,METEO.axis_text

    return "#fff","","black"


def write_cloudgram_png(data: CloudgramData) -> Path:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle

    apply_meteo_rcparams(plt)

    n_cols=len(data.cells)
    n_rows=len(CLOUDGRAM_ROWS)

    fig,ax=plt.subplots(figsize=(max(12,n_cols*0.5),7),facecolor=METEO.figure_bg)
    ax.set_facecolor(METEO.axes_bg)

    for y,row in enumerate(CLOUDGRAM_ROWS):
        for x,cell in enumerate(data.cells):
            fc,t,tc=_cell(row,cell)
            ax.add_patch(Rectangle((x-0.5,y-0.5),1,1,facecolor=fc,edgecolor="#fff",linewidth=0.6))
            ax.text(x,y,t,ha="center",va="center",fontsize=7,color=tc)

    ax.set_xlim(-0.5,n_cols-0.5)
    ax.set_ylim(n_rows-0.5,-0.5)

    ax.set_xticks(range(n_cols))
    ax.set_xticklabels([f"+{c.lead_hour}" for c in data.cells],rotation=90,fontsize=7)
    ax.set_yticks(range(n_rows))
    ax.set_yticklabels([f"{r.label}\n{r.unit}" for r in CLOUDGRAM_ROWS],fontsize=8)

    ax.set_xlabel("UTC")
    ax.set_ylabel("Параметр")
    ax.set_title("cloudgram GFS")

    style_axis(ax,grid=False)
    ax.tick_params(length=0)

    add_footer(fig,"cloud %, precip, cb, ceiling, vis, phen")

    p=Path(tempfile.NamedTemporaryFile(delete=False,suffix=".png").name)
    fig.savefig(p,dpi=180,bbox_inches="tight")
    plt.close(fig)
    return p