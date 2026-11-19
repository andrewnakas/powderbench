"""JMA adapter: both archive page layouts, value marks, cm -> in, caching."""

from datetime import date

from powderbench.networks import jma


def _row(cells):
    return '<tr class="mtx" style="text-align:right;">' + "".join(f"<td>{c}</td>" for c in cells) + "</tr>"


def _a_page(days):
    head = _row(["日", "降水量"]) + _row(["合計(mm)"])
    body = "".join(_row([str(d)] + ["0.0"] * 15 + [snow, "100"]) for d, snow in days)
    return f"<table>{head}{body}</table>"


def _s_page(days):
    body = "".join(_row([str(d)] + ["0.0"] * 16 + [snow, "50", "雪", "曇"]) for d, snow in days)
    return f"<table>{body}</table>"


def test_parse_value_marks():
    assert jma.parse_value("12") == 12.0
    assert jma.parse_value("7 )") == 7.0  # quasi-normal: kept
    assert jma.parse_value("--") == 0.0  # no phenomenon
    for missing in ("3 ]", "///", "", "×", "5#"):
        assert jma.parse_value(missing) is None


def test_parse_both_layouts():
    a = jma.parse_month(_a_page([(1, "25"), (2, "--"), (3, "///")]), "a")
    assert a == {1: 25.0, 2: 0.0, 3: None}
    s = jma.parse_month(_s_page([(1, "10 )"), (2, "4 ]")]), "s")
    assert s == {1: 10.0, 2: None}


def test_daily_units_and_cache(tmp_path, monkeypatch):
    calls = []

    def fake_get(kind, prec, block, year, month):
        calls.append((kind, prec, block, year, month))
        return _a_page([(10, "25.4"), (11, "--"), (12, "///")])

    monkeypatch.setattr(jma, "_get", fake_get)
    monkeypatch.setattr(jma, "data_dir", lambda: tmp_path)
    stations = {"31482:JP:JMA": "a:31:1079"}
    out = jma.daily(stations, date(2025, 1, 10), date(2025, 1, 12)).set_index("date")
    assert out.loc[date(2025, 1, 10), "snow24"] == 10.0  # 25.4 cm = 10 in
    assert out.loc[date(2025, 1, 11), "snow24"] == 0.0
    assert not out.loc[date(2025, 1, 12), "valid"]
    n = len(calls)
    jma.daily(stations, date(2025, 1, 10), date(2025, 1, 12))
    assert len(calls) == n  # past month served from the cache
