"""DWD adapter: zip parsing, -999 missing, morning-reading offset."""

import io
import zipfile
from datetime import date

from powderbench.networks import dwd


def _zip(rows):
    body = "STATIONS_ID;MESS_DATUM;QN_6;  RS; RSF;SH_TAG;NSH_TAG;eor\n" + "".join(
        f"       1734;{d};    1;   0.0;   6;   0;{v};eor\n" for d, v in rows)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("produkt_nieder_tag_20250101_20250131_01734.txt", body.encode("latin1"))
        zf.writestr("Metadaten_Geographie_01734.txt", "x")
    return buf.getvalue()


def test_parse_and_offset(monkeypatch):
    rec = dwd.parse_zip(_zip([("20250110", "0"), ("20250111", "  25"), ("20250112", "-999")]))
    assert rec["nsh_cm"].isna().tolist() == [False, False, True]
    monkeypatch.setattr(dwd, "record", lambda code: rec.set_index("date")["nsh_cm"])
    t = dwd.daily(["1734:DE:DWD"], date(2025, 1, 9), date(2025, 1, 11)).set_index("date")
    assert t.loc[date(2025, 1, 9), "snow24"] == 0.0  # read the morning of the 10th
    assert abs(t.loc[date(2025, 1, 10), "snow24"] - 25 / 2.54) < 1e-3  # read the morning of the 11th
    assert not t.loc[date(2025, 1, 11), "valid"]  # -999 on the 12th
