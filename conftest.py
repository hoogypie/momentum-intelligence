"""
Root conftest.py — pytest path configuration.
Zorgt dat `scoring.scoring_v1_2` importeerbaar is vanuit tests/.
Altijd aanwezig; nooit features toevoegen aan dit bestand.
"""
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))
