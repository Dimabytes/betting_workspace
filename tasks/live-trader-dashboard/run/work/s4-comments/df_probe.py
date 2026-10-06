"""Probe: does st.dataframe leak Unknown under strict basedpyright?"""
import streamlit as st
import pandas as pd

df = pd.DataFrame({"a": [1]})
out = st.dataframe(df, width="stretch", hide_index=True)
reveal_type(out)
