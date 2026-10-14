"""Тесты прежних HTML-страниц поверх новой БД-архитектуры (без legacy-моста):
регистрация (round-trip urlencoded-формы), судейская форма (details-контракт),
табло, главное табло, результаты, админ-API, data-editor."""
from __future__ import annotations

import pytest

import app as app_module
from models import db


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module.config, "SQLALCHEMY_DATABASE_URI",
                        f"sqlite:///{tmp_path / 'test.db'}")
    app_module.app.config["SQLALCHEMY_DATABASE_URI"] = \
        f"sqlite:///{tmp_path / 'test.db'}"
    app_module.app.config["TESTING"] = True
    with app_module.app.app_context():
        db.engine.dispose()
        db.create_all()
    yield app_module.app.test_client()
    with app_module.app.app_context():
        db.session.remove()
        db.drop_all()


def login(client):  # type: ignore[no-untyped-def]
    r = client.post("/api/login", json={"password": "admin123"})
    assert r.status_code == 200


def make_competition(client, comp="Cup1", kata="nage-no-kata"):  # type: ignore[no-untyped-def]
    login(client)
    r = client.post("/admin/create", data={"name": comp, "folder": comp,
                                           "date": "2026-10-14"})
    assert r.status_code in (200, 302)
    r = client.post(f"/admin/{comp}/add-discipline",
                    json={"kata_key": kata, "display_name": "Nage-no-kata"})
    assert r.status_code == 200


# ---------- страницы ----------

def test_login_and_dashboard_pages(client):  # type: ignore[no-untyped-def]
    assert client.get("/login").status_code == 200
    assert client.get("/dashboard").status_code == 200
    login(client)
    assert client.get("/admin").status_code == 200
    assert client.get("/admin/new").status_code == 200


def test_edit_competition_page(client):  # type: ignore[no-untyped-def]
    make_competition(client)
    r = client.get("/admin/Cup1")
    assert r.status_code == 200
    assert b"Nage" in r.data or b"nage" in r.data


def test_register_roundtrip_form(client):  # type: ignore[no-untyped-def]
    """Прежняя urlencoded-форма регистрации -> БД -> registration-data."""
    make_competition(client)
    form = {
        "pair_0_tori_name": "Ivan Petrov", "pair_0_tori_birth": "2005-01-02",
        "pair_0_tori_rank": "КМС", "pair_0_tori_kyu": "1 кю",
        "pair_0_tori_school": "SSh1", "pair_0_tori_coach": "Coach A",
        "pair_0_uke_name": "Petr Ivanov", "pair_0_uke_birth": "2005",
        "pair_0_uke_rank": "1 разряд", "pair_0_uke_kyu": "",
        "pair_0_uke_school": "SSh1", "pair_0_uke_coach": "Coach A",
        "pair_1_tori_name": "Sid Sidorov", "pair_1_uke_name": "Oleg Olegov",
        "judge_0_name": "Judge One", "judge_1_name": "Judge Two",
    }
    r = client.post("/register/Cup1/nage-no-kata", data=form)
    assert r.status_code in (200, 302)
    d = client.get("/api/Cup1/nage-no-kata/registration-data").get_json()
    pairs = d["existing_pairs"]
    assert len(pairs) == 2
    assert pairs[0]["Тори_ФИО"] == "Ivan Petrov"
    assert str(pairs[0]["Тори_год рождения"]).startswith("2005")
    assert pairs[0]["Тори_СШ"] == "SSh1"
    assert pairs[1]["Уке_ФИО"] == "Oleg Olegov"
    assert {j["ФИО"] for j in d["existing_judges"]} == {"Judge One", "Judge Two"}
    page = client.get("/register/Cup1/nage-no-kata")
    assert page.status_code == 200


def test_registration_autocomplete_apis(client):  # type: ignore[no-untyped-def]
    login(client)
    client.post("/api/data/participants", json={
        "name": "Ivan Petrov", "birth_year": 2005, "sports_school": "SSh1"})
    assert client.get("/api/participants/search?q=ivan").status_code == 200
    sug = client.get("/api/participants/column-suggestions?field=sports_school&q=ssh")
    assert sug.status_code == 200 and "SSh1" in sug.get_json()
    info = client.get("/api/participants/info?name=Ivan Petrov").get_json()
    assert info["ФИО"] == "Ivan Petrov" and info["СШ"] == "SSh1"
    assert client.get("/api/judges/search?q=j").status_code == 200


def test_judge_form_save_and_load(client):  # type: ignore[no-untyped-def]
    """save-judge-action принимает массив state'ов из judge_form.html и
    get-judge-scores отдаёт его обратно по имени техники (round-trip)."""
    make_competition(client)
    client.post("/register/Cup1/nage-no-kata", data={
        "pair_0_tori_name": "T1", "pair_0_uke_name": "U1",
        "judge_0_name": "Judge One"})
    page = client.get("/judge/Cup1/nage-no-kata")
    assert page.status_code == 200
    from technics import DISCIPLINE_ROWS_BY_KEY, _disc_key
    techs = DISCIPLINE_ROWS_BY_KEY[_disc_key("nage-no-kata")][:10]
    details = [{"m1": 0, "m2": 0, "med": 0, "big": 0, "c_plus": 0,
                "c_minus": 0, "forgotten": False} for _ in techs]
    details[0]["med"] = 1.0          # штраф 1.0 на первой технике
    r = client.post("/api/Cup1/nage-no-kata/save-judge-action", json={
        "judge": "Judge One", "pos": 1, "pair": 1,
        "tori": "T1", "uke": "U1", "details": details})
    assert r.status_code == 200
    body = r.get_json()
    assert body["ok"] is True
    assert abs(body["total"] - 99.0) < 1e-6   # 10*10 - 1.0
    load = client.get("/api/Cup1/nage-no-kata/get-judge-scores/"
                      "Judge%20One/1/T1/U1?pair=1").get_json()
    assert load[techs[0]]["med"] == 1.0
    assert load[techs[1]]["forgotten"] is False


def test_tablo_and_main_tablo(client):  # type: ignore[no-untyped-def]
    make_competition(client)
    client.post("/register/Cup1/nage-no-kata", data={
        "pair_0_tori_name": "T1", "pair_0_uke_name": "U1",
        "judge_0_name": "Judge One"})
    client.post("/api/Cup1/nage-no-kata/save-judge-action", json={
        "judge": "Judge One", "pos": 1, "pair": 1, "details": []})
    for url in ("/tablo/Cup1/nage-no-kata", "/tablo/Cup1/nage-no-kata/qual",
                "/results/Cup1/nage-no-kata"):
        assert client.get(url).status_code == 200
    js = client.get("/tablo/Cup1/nage-no-kata",
                    headers={"X-Requested-With": "fetch"})
    assert js.status_code == 200
    payload = js.get_json()
    assert payload["stage"] == "qual"
    assert payload["judges"][0]["место"] == 1
    assert payload["results"][0]["tori_cell"]["name"] == "T1"
    # главное табло: пусто -> empty-страница, после set-main-tablo -> dynamic
    assert client.get("/main-tablo/Cup1").status_code == 200
    client.post("/admin/Cup1/set-main-tablo", json={"kata_key": "nage-no-kata"})
    st = client.get("/api/main-tablo/Cup1/state").get_json()
    assert st["discipline"] == "nage-no-kata" and "<table" in st["html"].lower() or st["html"]


def test_stage_flow_and_protocols(client):  # type: ignore[no-untyped-def]
    make_competition(client)
    client.post("/register/Cup1/nage-no-kata", data={
        "pair_0_tori_name": "T1", "pair_0_uke_name": "U1"})
    r = client.post("/admin/Cup1/nage-no-kata/stage",
                    json={"action": "open_final"})
    assert r.status_code == 200
    d = client.get("/api/Cup1/nage-no-kata/registration-data").get_json()
    assert any(p["Тори_ФИО"] == "T1" for p in d["existing_pairs"])
    g = client.post("/admin/Cup1/generate-protocols", json={})
    assert g.status_code == 200
    files = g.get_json().get("files") or {}
    assert files.get("csv") and files.get("xlsx") and files.get("pdf")
    dl = client.get("/downloads/" + files["csv"].split("/")[-1])
    assert dl.status_code == 200


def test_admin_clear_and_delete(client):  # type: ignore[no-untyped-def]
    make_competition(client)
    login(client)
    client.post("/api/data/participants", json={"name": "A A"})
    client.post("/api/data/judges", json={"name": "J J"})
    assert client.post("/admin/clear-participants").status_code == 200
    assert client.post("/admin/clear-judges").status_code == 200
    assert client.get("/api/data/participants").get_json() == []
    assert client.get("/api/data/judges").get_json() == []
    r = client.post("/admin/Cup1/delete")
    assert r.status_code == 200
    assert client.get("/admin/Cup1").status_code == 404


def test_data_editor_page_and_crud(client):  # type: ignore[no-untyped-def]
    login(client)
    assert client.get("/data-editor").status_code == 200
    p = client.post("/api/data/participants",
                    json={"ФИО": "Test User", "год рождения": "2000"}).get_json()
    pid = p["id"]
    u = client.put(f"/api/data/participants/{pid}",
                   json={"ФИО": "Test User2"}).get_json()
    assert u["ФИО"] == "Test User2"
    assert client.delete(f"/api/data/participants/{pid}").status_code == 200
    j = client.post("/api/data/judges", json={"ФИО": "JJ", "категория": "1"})
    assert j.status_code == 200
    assert client.get("/api/data/competitions").status_code == 200


def test_close_open_competition(client):  # type: ignore[no-untyped-def]
    make_competition(client)
    assert client.post("/admin/Cup1/close").status_code == 200
    page = client.get("/admin/Cup1")
    assert b"close" in page.data or b"\xd7\xd7" in page.data  # статус в разметке
    assert client.post("/admin/Cup1/open").status_code == 200
