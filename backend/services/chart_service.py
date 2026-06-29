import plotly.graph_objects as go
import plotly.express as px
import pandas as pd


def _error(message: str) -> dict:
    return {"error": message}


def create_bar_chart(data: dict, title: str = "Bar Chart") -> dict:
    if "error" in data:
        return data

    if "groups" not in data or not data["groups"]:
        return _error("Для построения графика недостаточно данных.")

    groups = data["groups"]
    x_vals = list(groups.keys())
    y_vals = list(groups.values())

    fig = go.Figure(data=[go.Bar(x=x_vals, y=y_vals)])
    fig.update_layout(title=title)

    return {"chart_type": "bar", "plotly_json": fig.to_json()}


def create_line_chart(data: dict, title: str = "Line Chart") -> dict:
    if "error" in data:
        return data

    if "groups" not in data or not data["groups"]:
        return _error("Для построения графика недостаточно данных.")

    groups = data["groups"]
    x_vals = list(groups.keys())
    y_vals = list(groups.values())

    fig = go.Figure(data=[go.Scatter(x=x_vals, y=y_vals, mode="lines+markers")])
    fig.update_layout(title=title)

    return {"chart_type": "line", "plotly_json": fig.to_json()}


def create_pie_chart(data: dict, title: str = "Pie Chart") -> dict:
    if "error" in data:
        return data

    if "groups" not in data or not data["groups"]:
        return _error("Для построения графика недостаточно данных.")

    groups = data["groups"]
    labels = list(groups.keys())
    values = list(groups.values())

    fig = go.Figure(data=[go.Pie(labels=labels, values=values)])
    fig.update_layout(title=title)

    return {"chart_type": "pie", "plotly_json": fig.to_json()}


def create_top_clients_chart(data: dict) -> dict:
    return create_bar_chart(data, title="Топ клиентов")


def create_region_chart(data: dict) -> dict:
    return create_pie_chart(data, title="Продажи по регионам")


def create_manager_chart(data: dict) -> dict:
    return create_bar_chart(data, title="Продажи по менеджерам")


def create_monthly_trend_chart(data: dict) -> dict:
    return create_line_chart(data, title="Динамика продаж по месяцам")
