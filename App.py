import streamlit as st
import pandas as pd

st.set_page_config(page_title="Body Comp Tracker", layout="wide")

st.title("📊 Body Composition Tracker")

# Load data
data = pd.read_csv("data.csv", parse_dates=["date"])

# Derived metrics
data["fat_mass"] = (data["weight"] * (data["body_fat"] / 100)).round(2)
data["lean_mass"] = (data["weight"] - data["fat_mass"]).round(2)

st.subheader("Raw Data")
st.dataframe(data)

st.subheader("Weight Trend")
st.line_chart(data.set_index("date")[["weight", "lean_mass"]])

st.subheader("Body Fat %")
st.line_chart(data.set_index("date")[["body_fat"]])
