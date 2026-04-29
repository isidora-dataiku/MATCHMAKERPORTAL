# -*- coding: utf-8 -*-
import dataiku
import pandas as pd, numpy as np
from dataiku import pandasutils as pdu

# Read recipe inputs
advisor_feedback = dataiku.Dataset("advisor_feedback")
advisor_feedback_df = advisor_feedback.get_dataframe()

# 1. Filter out nulls/empty strings first to avoid joining empty text
df = advisor_feedback_df[
    (advisor_feedback_df['afwijzing_toelichting'].notna()) & 
    (advisor_feedback_df['afwijzing_toelichting'] != "")
].copy()

# 2. Group by candidate_id and concatenate the strings
# This uses a lambda to join all feedback notes with a separator (e.g., " | ")
advisor_feedback_filtered_df = df.groupby('candidate_id').agg({
    'afwijzing_toelichting': lambda x: " | ".join(x.astype(str))
}).reset_index()

# Note: If you have other columns you want to keep, you'll need to specify 
# how to handle them (e.g., 'first', 'max', etc.) in the .agg() dictionary.

# Write recipe outputs
advisor_feedback_filtered = dataiku.Dataset("advisor_feedback_filtered")
advisor_feedback_filtered.write_with_schema(advisor_feedback_filtered_df)