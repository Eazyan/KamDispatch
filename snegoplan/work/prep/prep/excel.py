"""Загрузка snegoplan_input.xlsx (openpyxl) в плоские структуры."""
from openpyxl import load_workbook


def _rows(ws):
    for row in ws.iter_rows(values_only=True):
        if all(v is None for v in row):
            continue
        yield list(row)


def load_excel(path):
    """Возвращает dict: types, machines, points, conditions, density."""
    wb = load_workbook(path, data_only=True)

    # --- Типы техники ---
    types = []
    ws = wb["Типы техники"]
    it = _rows(ws)
    header = next(it)
    for row in it:
        if not isinstance(row[0], str) or row[1] is None:
            continue  # примечания внизу листа
        rec = dict(zip(header, row))
        types.append({
            "model": rec["тип (модель)"],
            "class": rec["класс"],
            "gabarit_m": float(rec["ширина_габарит_м"]),
            "work_width_min_m": float(rec["мин_ширина_уборки_м"]),
            "work_width_max_m": float(rec["макс_ширина_уборки_м"]),
            "snow_max_cm": float(rec["макс_высота_снега_см"] or 0),
            "v_work_kmh": float(rec["скорость_уборки_кмч"] or 0),
            "speed_coef": float(rec["коэф_скорости"] or 1),
            "h_ref_cm": float(rec["href_снега_см"] or 20),
            "v_transit_kmh": float(rec["скорость_перегона_кмч"] or 0),
            "source": rec.get("источник"),
        })

    # --- Машины ---
    machines = []
    ws = wb["Машины"]
    it = _rows(ws)
    header = next(it)
    for row in it:
        if not isinstance(row[0], str) or not str(row[0]).strip() \
                or not str(row[0]).strip()[0].isalnum():
            continue  # пустые строки и примечания («Все строки - пример...»)
        rec = dict(zip(header, row))
        if rec.get("старт_lat") is None or rec.get("финиш_lat") is None:
            continue  # строка-примечание с текстом в первой колонке
        machines.append({
            "id": str(rec["id"]).strip(),
            "type": rec["тип"],
            "count": int(rec["кол-во"] or 1),
            "start_lat": float(rec["старт_lat"]),
            "start_lon": float(rec["старт_lon"]),
            "finish_lat": float(rec["финиш_lat"]),
            "finish_lon": float(rec["финиш_lon"]),
            "start_addr": rec["старт_адрес"],
            "finish_addr": rec["финиш_адрес"],
            "driver": rec.get("водитель"),
        })

    # --- Точки ---
    points = []
    ws = wb["Точки"]
    it = _rows(ws)
    header = next(it)
    for row in it:
        if not isinstance(row[0], str) or not str(row[0]).strip() \
                or not str(row[0]).strip()[0].isalnum():
            continue  # пустые строки и примечания
        rec = dict(zip(header, row))
        if rec.get("lat") is None:
            continue
        points.append({
            "id": rec["id"], "kind": rec["вид"],
            "lat": float(rec["lat"]), "lon": float(rec["lon"]),
            "name": rec["название"],
        })

    # --- Условия ---
    conditions = {}
    density = {}
    ws = wb["Условия"]
    it = _rows(ws)
    header = next(it)
    for row in it:
        if isinstance(row[0], str) and row[1] is not None and row[2] is not None:
            conditions[row[0]] = {
                "snow_h_cm": float(row[1]),
                "traffic_coef": float(row[2]),
                "density_name": row[3],
            }
        elif isinstance(row[0], str) and row[1] is not None and row[2] is None:
            try:
                density[row[0]] = float(row[1])  # таблица плотностей кг/м3
            except (TypeError, ValueError):
                pass  # подзаголовок таблицы («кг/м3 (оценка)»)
    return {"types": types, "machines": machines,
            "points": points, "conditions": conditions, "density": density}
