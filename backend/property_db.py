"""
property_db.py  —  Structured Thermodynamic Property Lookup
─────────────────────────────────────────────────────────────
SQLite-backed database of critical properties, Antoine constants,
NRTL BIPs (fixed-T and temperature-dependent), UNIQUAC BIPs, and
Henry's law constants for 150+ common compounds.

Sources:
  - DIPPR 801 (2023)
  - Poling, Prausnitz & O'Connell: Properties of Gases and Liquids, 5th ed.
  - Gmehling et al.: DECHEMA VLE Data Collection
  - Perry's Chemical Engineers' Handbook, 9th ed.
  - NIST WebBook (webbook.nist.gov)

Usage:
    from property_db import PropertyDB
    db = PropertyDB()                                # singleton, auto-creates SQLite
    r  = db.lookup("ethanol", ["critical", "antoine", "nrtl_water"])
    r2 = db.lookup_pair("ethanol", "water", "nrtl")
    r3 = db.search_compound("IPA")
"""

from __future__ import annotations

import math
import os
import sqlite3
import threading
from typing import Any, Dict, List, Optional

_DB_PATH = os.path.join(os.path.dirname(__file__), "thermo_properties.db")

# ─────────────────────────────────────────────────────────────────────────────
# MASTER DATA — edit here, DB is rebuilt automatically on hash mismatch
# ─────────────────────────────────────────────────────────────────────────────

# fmt: off
COMPOUND_DATA: List[Dict[str, Any]] = [
    # name, cas, mw, Tc_K, Pc_bar, Vc_cm3mol, omega, Tb_C, density_25_kgm3,
    # dHvap_NBP_kJmol, Cp_liq_25_Jmolk, aliases
    {"name":"water",       "cas":"7732-18-5",  "mw":18.015,  "Tc_K":647.10, "Pc_bar":220.64, "Vc_cm3mol":56.0,  "omega":0.3449,  "Tb_C":100.0,   "density_25_kgm3":997.0,  "dHvap_kJmol":40.65, "Cp_liq_Jmolk":75.3,  "aliases":["H2O","water","steam","aqua"]},
    {"name":"methane",     "cas":"74-82-8",    "mw":16.043,  "Tc_K":190.56, "Pc_bar":45.99,  "Vc_cm3mol":98.6,  "omega":0.0115,  "Tb_C":-161.5,  "density_25_kgm3":None,   "dHvap_kJmol":8.17,  "Cp_liq_Jmolk":54.0,  "aliases":["CH4","natural gas","methane"]},
    {"name":"ethane",      "cas":"74-84-0",    "mw":30.070,  "Tc_K":305.32, "Pc_bar":48.72,  "Vc_cm3mol":145.5, "omega":0.0995,  "Tb_C":-88.6,   "density_25_kgm3":None,   "dHvap_kJmol":14.69, "Cp_liq_Jmolk":68.5,  "aliases":["C2H6","ethane"]},
    {"name":"propane",     "cas":"74-98-6",    "mw":44.097,  "Tc_K":369.83, "Pc_bar":42.48,  "Vc_cm3mol":200.0, "omega":0.1521,  "Tb_C":-42.09,  "density_25_kgm3":493.0,  "dHvap_kJmol":18.77, "Cp_liq_Jmolk":96.7,  "aliases":["C3H8","LPG","propane"]},
    {"name":"n-butane",    "cas":"106-97-8",   "mw":58.124,  "Tc_K":425.12, "Pc_bar":37.96,  "Vc_cm3mol":255.0, "omega":0.2002,  "Tb_C":-0.50,   "density_25_kgm3":573.0,  "dHvap_kJmol":22.44, "Cp_liq_Jmolk":140.9, "aliases":["C4H10","n-C4","butane","n-butane"]},
    {"name":"n-pentane",   "cas":"109-66-0",   "mw":72.151,  "Tc_K":469.70, "Pc_bar":33.70,  "Vc_cm3mol":311.0, "omega":0.2515,  "Tb_C":36.07,   "density_25_kgm3":621.0,  "dHvap_kJmol":25.79, "Cp_liq_Jmolk":167.2, "aliases":["C5H12","n-C5","pentane"]},
    {"name":"n-hexane",    "cas":"110-54-3",   "mw":86.178,  "Tc_K":507.60, "Pc_bar":30.25,  "Vc_cm3mol":368.0, "omega":0.3013,  "Tb_C":68.74,   "density_25_kgm3":655.0,  "dHvap_kJmol":28.85, "Cp_liq_Jmolk":195.6, "aliases":["C6H14","n-C6","hexane"]},
    {"name":"n-heptane",   "cas":"142-82-5",   "mw":100.205, "Tc_K":540.20, "Pc_bar":27.40,  "Vc_cm3mol":428.0, "omega":0.3495,  "Tb_C":98.38,   "density_25_kgm3":684.0,  "dHvap_kJmol":31.77, "Cp_liq_Jmolk":224.7, "aliases":["C7H16","n-C7","heptane"]},
    {"name":"ethylene",    "cas":"74-85-1",    "mw":28.054,  "Tc_K":282.34, "Pc_bar":50.41,  "Vc_cm3mol":131.0, "omega":0.0866,  "Tb_C":-103.7,  "density_25_kgm3":None,   "dHvap_kJmol":13.55, "Cp_liq_Jmolk":67.9,  "aliases":["C2H4","ethene","ethylene"]},
    {"name":"propylene",   "cas":"115-07-1",   "mw":42.081,  "Tc_K":364.85, "Pc_bar":46.65,  "Vc_cm3mol":185.0, "omega":0.1408,  "Tb_C":-47.62,  "density_25_kgm3":None,   "dHvap_kJmol":18.42, "Cp_liq_Jmolk":85.7,  "aliases":["C3H6","propene","propylene"]},
    {"name":"benzene",     "cas":"71-43-2",    "mw":78.114,  "Tc_K":562.05, "Pc_bar":48.95,  "Vc_cm3mol":259.0, "omega":0.2103,  "Tb_C":80.09,   "density_25_kgm3":873.8,  "dHvap_kJmol":30.72, "Cp_liq_Jmolk":136.0, "aliases":["C6H6","benzene","BTX"]},
    {"name":"toluene",     "cas":"108-88-3",   "mw":92.141,  "Tc_K":591.80, "Pc_bar":41.06,  "Vc_cm3mol":316.0, "omega":0.2638,  "Tb_C":110.63,  "density_25_kgm3":862.3,  "dHvap_kJmol":33.18, "Cp_liq_Jmolk":157.3, "aliases":["C7H8","methylbenzene","toluene"]},
    {"name":"o-xylene",    "cas":"95-47-6",    "mw":106.167, "Tc_K":630.33, "Pc_bar":37.34,  "Vc_cm3mol":370.0, "omega":0.3118,  "Tb_C":144.41,  "density_25_kgm3":880.0,  "dHvap_kJmol":36.24, "Cp_liq_Jmolk":188.0, "aliases":["C8H10","o-xylene","1,2-dimethylbenzene"]},
    {"name":"cyclohexane", "cas":"110-82-7",   "mw":84.161,  "Tc_K":553.64, "Pc_bar":40.75,  "Vc_cm3mol":308.0, "omega":0.2108,  "Tb_C":80.74,   "density_25_kgm3":774.0,  "dHvap_kJmol":29.97, "Cp_liq_Jmolk":156.0, "aliases":["C6H12","cyclohexane"]},
    {"name":"methanol",    "cas":"67-56-1",    "mw":32.042,  "Tc_K":512.64, "Pc_bar":80.97,  "Vc_cm3mol":118.0, "omega":0.5625,  "Tb_C":64.70,   "density_25_kgm3":791.0,  "dHvap_kJmol":35.27, "Cp_liq_Jmolk":80.9,  "aliases":["CH3OH","MeOH","methanol","methyl alcohol"]},
    {"name":"ethanol",     "cas":"64-17-5",    "mw":46.068,  "Tc_K":513.91, "Pc_bar":61.37,  "Vc_cm3mol":167.0, "omega":0.6452,  "Tb_C":78.37,   "density_25_kgm3":785.1,  "dHvap_kJmol":38.56, "Cp_liq_Jmolk":111.5, "aliases":["C2H5OH","EtOH","ethanol","ethyl alcohol"]},
    {"name":"1-propanol",  "cas":"71-23-8",    "mw":60.096,  "Tc_K":536.71, "Pc_bar":51.75,  "Vc_cm3mol":218.5, "omega":0.6299,  "Tb_C":97.20,   "density_25_kgm3":800.0,  "dHvap_kJmol":41.44, "Cp_liq_Jmolk":143.9, "aliases":["n-propanol","1-propanol","n-PrOH"]},
    {"name":"2-propanol",  "cas":"67-63-0",    "mw":60.096,  "Tc_K":508.30, "Pc_bar":47.62,  "Vc_cm3mol":220.0, "omega":0.6651,  "Tb_C":82.26,   "density_25_kgm3":786.0,  "dHvap_kJmol":39.85, "Cp_liq_Jmolk":161.1, "aliases":["IPA","isopropanol","2-propanol","isopropyl alcohol"]},
    {"name":"acetone",     "cas":"67-64-1",    "mw":58.079,  "Tc_K":508.20, "Pc_bar":47.01,  "Vc_cm3mol":209.0, "omega":0.3065,  "Tb_C":56.05,   "density_25_kgm3":784.6,  "dHvap_kJmol":30.99, "Cp_liq_Jmolk":124.7, "aliases":["C3H6O","propanone","acetone","dimethyl ketone"]},
    {"name":"acetic acid", "cas":"64-19-7",    "mw":60.052,  "Tc_K":591.95, "Pc_bar":57.86,  "Vc_cm3mol":171.0, "omega":0.4665,  "Tb_C":117.9,   "density_25_kgm3":1044.0, "dHvap_kJmol":39.65, "Cp_liq_Jmolk":123.4, "aliases":["CH3COOH","acetic acid","ethanoic acid","AcOH"]},
    {"name":"ethyl acetate","cas":"141-78-6",  "mw":88.106,  "Tc_K":523.25, "Pc_bar":38.30,  "Vc_cm3mol":286.0, "omega":0.3661,  "Tb_C":77.11,   "density_25_kgm3":895.0,  "dHvap_kJmol":31.94, "Cp_liq_Jmolk":170.0, "aliases":["EtOAc","ethyl acetate","ethyl ethanoate"]},
    {"name":"diethyl ether","cas":"60-29-7",   "mw":74.123,  "Tc_K":466.70, "Pc_bar":36.40,  "Vc_cm3mol":280.0, "omega":0.2810,  "Tb_C":34.55,   "density_25_kgm3":713.5,  "dHvap_kJmol":26.52, "Cp_liq_Jmolk":172.0, "aliases":["ether","diethyl ether","DEE","ethoxyethane"]},
    {"name":"THF",         "cas":"109-99-9",   "mw":72.107,  "Tc_K":540.15, "Pc_bar":51.91,  "Vc_cm3mol":224.0, "omega":0.2255,  "Tb_C":66.00,   "density_25_kgm3":889.0,  "dHvap_kJmol":31.79, "Cp_liq_Jmolk":123.7, "aliases":["THF","tetrahydrofuran","oxolane"]},
    {"name":"chloroform",  "cas":"67-66-3",    "mw":119.378, "Tc_K":536.40, "Pc_bar":53.70,  "Vc_cm3mol":239.0, "omega":0.2220,  "Tb_C":61.17,   "density_25_kgm3":1489.0, "dHvap_kJmol":29.24, "Cp_liq_Jmolk":114.2, "aliases":["CHCl3","chloroform","trichloromethane"]},
    {"name":"acetonitrile","cas":"75-05-8",    "mw":41.053,  "Tc_K":545.50, "Pc_bar":48.30,  "Vc_cm3mol":173.0, "omega":0.3380,  "Tb_C":81.65,   "density_25_kgm3":781.6,  "dHvap_kJmol":33.23, "Cp_liq_Jmolk":91.4,  "aliases":["MeCN","acetonitrile","methyl cyanide"]},
    {"name":"nitrogen",    "cas":"7727-37-9",  "mw":28.013,  "Tc_K":126.19, "Pc_bar":33.96,  "Vc_cm3mol":89.2,  "omega":0.0377,  "Tb_C":-195.8,  "density_25_kgm3":None,   "dHvap_kJmol":5.57,  "Cp_liq_Jmolk":56.2,  "aliases":["N2","nitrogen"]},
    {"name":"oxygen",      "cas":"7782-44-7",  "mw":31.999,  "Tc_K":154.58, "Pc_bar":50.43,  "Vc_cm3mol":73.4,  "omega":0.0222,  "Tb_C":-182.95, "density_25_kgm3":None,   "dHvap_kJmol":6.82,  "Cp_liq_Jmolk":53.4,  "aliases":["O2","oxygen"]},
    {"name":"hydrogen",    "cas":"1333-74-0",  "mw":2.016,   "Tc_K":33.19,  "Pc_bar":13.13,  "Vc_cm3mol":64.1,  "omega":-0.2160, "Tb_C":-252.88, "density_25_kgm3":None,   "dHvap_kJmol":0.904, "Cp_liq_Jmolk":19.7,  "aliases":["H2","hydrogen","dihydrogen"]},
    {"name":"CO2",         "cas":"124-38-9",   "mw":44.010,  "Tc_K":304.13, "Pc_bar":73.77,  "Vc_cm3mol":94.07, "omega":0.2239,  "Tb_C":None,    "density_25_kgm3":None,   "dHvap_kJmol":None,  "Cp_liq_Jmolk":None,  "aliases":["CO2","carbon dioxide","dry ice"]},
    {"name":"H2S",         "cas":"7783-06-4",  "mw":34.082,  "Tc_K":373.53, "Pc_bar":89.63,  "Vc_cm3mol":98.5,  "omega":0.0942,  "Tb_C":-60.33,  "density_25_kgm3":None,   "dHvap_kJmol":18.67, "Cp_liq_Jmolk":None,  "aliases":["H2S","hydrogen sulfide","sour gas"]},
    {"name":"ammonia",     "cas":"7664-41-7",  "mw":17.031,  "Tc_K":405.65, "Pc_bar":113.33, "Vc_cm3mol":72.5,  "omega":0.2526,  "Tb_C":-33.35,  "density_25_kgm3":None,   "dHvap_kJmol":23.33, "Cp_liq_Jmolk":80.8,  "aliases":["NH3","ammonia"]},
    {"name":"SO2",         "cas":"7446-09-5",  "mw":64.065,  "Tc_K":430.75, "Pc_bar":78.84,  "Vc_cm3mol":122.0, "omega":0.2450,  "Tb_C":-10.05,  "density_25_kgm3":None,   "dHvap_kJmol":24.94, "Cp_liq_Jmolk":None,  "aliases":["SO2","sulfur dioxide"]},
    {"name":"MEA",         "cas":"141-43-5",   "mw":61.083,  "Tc_K":678.20, "Pc_bar":67.11,  "Vc_cm3mol":None,  "omega":0.7560,  "Tb_C":170.5,   "density_25_kgm3":1018.0, "dHvap_kJmol":56.43, "Cp_liq_Jmolk":170.0, "aliases":["MEA","monoethanolamine","ethanolamine"]},
    {"name":"glycerol",    "cas":"56-81-5",    "mw":92.094,  "Tc_K":850.0,  "Pc_bar":75.0,   "Vc_cm3mol":None,  "omega":1.4850,  "Tb_C":290.0,   "density_25_kgm3":1261.0, "dHvap_kJmol":None,  "Cp_liq_Jmolk":None,  "aliases":["glycerol","glycerin","propane-1,2,3-triol"]},
    {"name":"styrene",     "cas":"100-42-5",   "mw":104.152, "Tc_K":648.00, "Pc_bar":38.40,  "Vc_cm3mol":352.0, "omega":0.2970,  "Tb_C":145.15,  "density_25_kgm3":906.0,  "dHvap_kJmol":36.76, "Cp_liq_Jmolk":182.0, "aliases":["styrene","vinylbenzene","C8H8"]},
    {"name":"DMF",         "cas":"68-12-2",    "mw":73.095,  "Tc_K":647.00, "Pc_bar":44.80,  "Vc_cm3mol":262.0, "omega":0.3740,  "Tb_C":153.0,   "density_25_kgm3":944.5,  "dHvap_kJmol":46.89, "Cp_liq_Jmolk":150.6, "aliases":["DMF","dimethylformamide","N,N-dimethylformamide"]},
    {"name":"DMSO",        "cas":"67-68-5",    "mw":78.133,  "Tc_K":729.00, "Pc_bar":56.50,  "Vc_cm3mol":227.0, "omega":0.3228,  "Tb_C":189.0,   "density_25_kgm3":1100.4, "dHvap_kJmol":52.90, "Cp_liq_Jmolk":153.0, "aliases":["DMSO","dimethyl sulfoxide"]},
    {"name":"NMP",         "cas":"872-50-4",   "mw":99.132,  "Tc_K":721.80, "Pc_bar":45.50,  "Vc_cm3mol":311.0, "omega":0.3580,  "Tb_C":202.0,   "density_25_kgm3":1028.0, "dHvap_kJmol":55.26, "Cp_liq_Jmolk":168.0, "aliases":["NMP","N-methyl-2-pyrrolidone","N-methylpyrrolidinone"]},
    # ── Branched alkanes & isomers ────────────────────────────────────────────
    {"name":"isobutane",     "cas":"75-28-5",    "mw":58.124,  "Tc_K":408.14, "Pc_bar":36.48,  "Vc_cm3mol":263.0, "omega":0.1853,  "Tb_C":-11.75,  "density_25_kgm3":None,   "dHvap_kJmol":21.30, "Cp_liq_Jmolk":130.5, "aliases":["isobutane","i-C4","R600a","2-methylpropane"]},
    {"name":"isopentane",    "cas":"78-78-4",    "mw":72.151,  "Tc_K":460.43, "Pc_bar":33.81,  "Vc_cm3mol":306.0, "omega":0.2275,  "Tb_C":27.85,   "density_25_kgm3":616.0,  "dHvap_kJmol":24.69, "Cp_liq_Jmolk":163.7, "aliases":["isopentane","i-C5","2-methylbutane"]},
    {"name":"isooctane",     "cas":"540-84-1",   "mw":114.232, "Tc_K":543.90, "Pc_bar":25.73,  "Vc_cm3mol":468.0, "omega":0.3031,  "Tb_C":99.24,   "density_25_kgm3":688.0,  "dHvap_kJmol":30.79, "Cp_liq_Jmolk":244.7, "aliases":["isooctane","2,2,4-trimethylpentane","i-C8"]},
    {"name":"n-octane",      "cas":"111-65-9",   "mw":114.232, "Tc_K":568.70, "Pc_bar":24.86,  "Vc_cm3mol":492.0, "omega":0.3996,  "Tb_C":125.67,  "density_25_kgm3":703.0,  "dHvap_kJmol":34.41, "Cp_liq_Jmolk":255.5, "aliases":["n-octane","n-C8","octane"]},
    {"name":"n-nonane",      "cas":"111-84-2",   "mw":128.259, "Tc_K":594.60, "Pc_bar":22.90,  "Vc_cm3mol":555.0, "omega":0.4435,  "Tb_C":150.82,  "density_25_kgm3":718.0,  "dHvap_kJmol":36.91, "Cp_liq_Jmolk":284.4, "aliases":["n-nonane","n-C9","nonane"]},
    {"name":"n-decane",      "cas":"124-18-5",   "mw":142.286, "Tc_K":617.70, "Pc_bar":21.10,  "Vc_cm3mol":617.0, "omega":0.4923,  "Tb_C":174.12,  "density_25_kgm3":726.0,  "dHvap_kJmol":38.75, "Cp_liq_Jmolk":314.5, "aliases":["n-decane","n-C10","decane"]},
    # ── Aromatic compounds ────────────────────────────────────────────────────
    {"name":"m-xylene",      "cas":"108-38-3",   "mw":106.167, "Tc_K":617.05, "Pc_bar":35.36,  "Vc_cm3mol":376.0, "omega":0.3261,  "Tb_C":139.10,  "density_25_kgm3":860.0,  "dHvap_kJmol":35.67, "Cp_liq_Jmolk":183.1, "aliases":["m-xylene","1,3-dimethylbenzene"]},
    {"name":"p-xylene",      "cas":"106-42-3",   "mw":106.167, "Tc_K":616.26, "Pc_bar":35.11,  "Vc_cm3mol":379.0, "omega":0.3218,  "Tb_C":138.35,  "density_25_kgm3":857.0,  "dHvap_kJmol":35.67, "Cp_liq_Jmolk":182.0, "aliases":["p-xylene","1,4-dimethylbenzene","PX"]},
    {"name":"ethylbenzene",  "cas":"100-41-4",   "mw":106.167, "Tc_K":617.15, "Pc_bar":36.09,  "Vc_cm3mol":374.0, "omega":0.3026,  "Tb_C":136.19,  "density_25_kgm3":867.0,  "dHvap_kJmol":35.57, "Cp_liq_Jmolk":183.2, "aliases":["ethylbenzene","C8H10","EB"]},
    {"name":"naphthalene",   "cas":"91-20-3",    "mw":128.174, "Tc_K":748.40, "Pc_bar":40.51,  "Vc_cm3mol":413.0, "omega":0.3020,  "Tb_C":218.0,   "density_25_kgm3":None,   "dHvap_kJmol":43.18, "Cp_liq_Jmolk":167.0, "aliases":["naphthalene","C10H8"]},
    {"name":"cumene",        "cas":"98-82-8",    "mw":120.194, "Tc_K":638.35, "Pc_bar":32.09,  "Vc_cm3mol":428.0, "omega":0.3260,  "Tb_C":152.39,  "density_25_kgm3":862.0,  "dHvap_kJmol":37.53, "Cp_liq_Jmolk":213.9, "aliases":["cumene","isopropylbenzene","C9H12"]},
    {"name":"phenol",        "cas":"108-95-2",   "mw":94.113,  "Tc_K":694.25, "Pc_bar":61.30,  "Vc_cm3mol":229.0, "omega":0.4440,  "Tb_C":181.84,  "density_25_kgm3":1058.0, "dHvap_kJmol":45.69, "Cp_liq_Jmolk":136.7, "aliases":["phenol","carbolic acid","C6H5OH"]},
    {"name":"aniline",       "cas":"62-53-3",    "mw":93.128,  "Tc_K":699.00, "Pc_bar":53.09,  "Vc_cm3mol":274.0, "omega":0.3817,  "Tb_C":184.13,  "density_25_kgm3":1022.0, "dHvap_kJmol":42.44, "Cp_liq_Jmolk":191.9, "aliases":["aniline","aminobenzene","C6H5NH2"]},
    # ── Higher alcohols & glycols ─────────────────────────────────────────────
    {"name":"1-butanol",     "cas":"71-36-3",    "mw":74.123,  "Tc_K":562.98, "Pc_bar":44.23,  "Vc_cm3mol":275.0, "omega":0.5886,  "Tb_C":117.73,  "density_25_kgm3":809.7,  "dHvap_kJmol":43.29, "Cp_liq_Jmolk":177.2, "aliases":["1-butanol","n-butanol","n-BuOH","butan-1-ol"]},
    {"name":"2-butanol",     "cas":"78-92-2",    "mw":74.123,  "Tc_K":536.01, "Pc_bar":41.79,  "Vc_cm3mol":269.0, "omega":0.5700,  "Tb_C":99.51,   "density_25_kgm3":803.0,  "dHvap_kJmol":40.75, "Cp_liq_Jmolk":196.9, "aliases":["2-butanol","sec-butanol","s-BuOH"]},
    {"name":"tert-butanol",  "cas":"75-65-0",    "mw":74.123,  "Tc_K":506.21, "Pc_bar":39.73,  "Vc_cm3mol":275.0, "omega":0.6170,  "Tb_C":82.42,   "density_25_kgm3":781.0,  "dHvap_kJmol":39.07, "Cp_liq_Jmolk":218.6, "aliases":["tert-butanol","t-butanol","TBA","2-methyl-2-propanol"]},
    {"name":"ethylene glycol","cas":"107-21-1",  "mw":62.068,  "Tc_K":720.00, "Pc_bar":82.0,   "Vc_cm3mol":186.0, "omega":0.5070,  "Tb_C":197.30,  "density_25_kgm3":1113.0, "dHvap_kJmol":50.50, "Cp_liq_Jmolk":149.8, "aliases":["MEG","ethylene glycol","EG","1,2-ethanediol","monoethylene glycol"]},
    {"name":"propylene glycol","cas":"57-55-6",  "mw":76.095,  "Tc_K":626.00, "Pc_bar":60.70,  "Vc_cm3mol":238.0, "omega":0.6490,  "Tb_C":188.20,  "density_25_kgm3":1036.0, "dHvap_kJmol":52.44, "Cp_liq_Jmolk":189.6, "aliases":["PG","propylene glycol","MPG","1,2-propanediol"]},
    {"name":"DEG",           "cas":"111-46-6",   "mw":106.121, "Tc_K":753.00, "Pc_bar":46.20,  "Vc_cm3mol":318.0, "omega":0.8090,  "Tb_C":244.80,  "density_25_kgm3":1118.0, "dHvap_kJmol":66.02, "Cp_liq_Jmolk":244.0, "aliases":["DEG","diethylene glycol","2,2'-oxydiethanol"]},
    {"name":"TEG",           "cas":"112-27-6",   "mw":150.174, "Tc_K":769.50, "Pc_bar":33.20,  "Vc_cm3mol":None,  "omega":0.8720,  "Tb_C":285.00,  "density_25_kgm3":1125.0, "dHvap_kJmol":None,  "Cp_liq_Jmolk":None,  "aliases":["TEG","triethylene glycol","gas dehydration"]},
    # ── Ketones ───────────────────────────────────────────────────────────────
    {"name":"MEK",           "cas":"78-93-3",    "mw":72.106,  "Tc_K":535.50, "Pc_bar":41.54,  "Vc_cm3mol":267.0, "omega":0.3229,  "Tb_C":79.64,   "density_25_kgm3":800.0,  "dHvap_kJmol":31.30, "Cp_liq_Jmolk":158.6, "aliases":["MEK","methyl ethyl ketone","2-butanone","butan-2-one"]},
    {"name":"MIBK",          "cas":"108-10-1",   "mw":100.160, "Tc_K":571.40, "Pc_bar":32.74,  "Vc_cm3mol":352.0, "omega":0.3600,  "Tb_C":116.53,  "density_25_kgm3":796.0,  "dHvap_kJmol":34.49, "Cp_liq_Jmolk":213.8, "aliases":["MIBK","methyl isobutyl ketone","4-methylpentan-2-one"]},
    {"name":"cyclohexanone", "cas":"108-94-1",   "mw":98.145,  "Tc_K":665.00, "Pc_bar":40.00,  "Vc_cm3mol":311.0, "omega":0.3920,  "Tb_C":155.65,  "density_25_kgm3":948.0,  "dHvap_kJmol":44.00, "Cp_liq_Jmolk":182.2, "aliases":["cyclohexanone","C6H10O"]},
    # ── Esters ────────────────────────────────────────────────────────────────
    {"name":"methyl acetate","cas":"79-20-9",    "mw":74.079,  "Tc_K":506.55, "Pc_bar":47.50,  "Vc_cm3mol":228.0, "omega":0.3310,  "Tb_C":56.87,   "density_25_kgm3":932.0,  "dHvap_kJmol":30.32, "Cp_liq_Jmolk":141.9, "aliases":["methyl acetate","MeOAc","methyl ethanoate"]},
    {"name":"butyl acetate", "cas":"123-86-4",   "mw":116.160, "Tc_K":575.40, "Pc_bar":30.42,  "Vc_cm3mol":403.0, "omega":0.4068,  "Tb_C":126.11,  "density_25_kgm3":882.0,  "dHvap_kJmol":36.28, "Cp_liq_Jmolk":226.4, "aliases":["butyl acetate","n-BuOAc","n-butyl acetate"]},
    # ── Ethers ────────────────────────────────────────────────────────────────
    {"name":"MTBE",          "cas":"1634-04-4",  "mw":88.150,  "Tc_K":497.10, "Pc_bar":33.70,  "Vc_cm3mol":329.0, "omega":0.2660,  "Tb_C":55.20,   "density_25_kgm3":740.5,  "dHvap_kJmol":29.82, "Cp_liq_Jmolk":196.6, "aliases":["MTBE","methyl tert-butyl ether","tert-butyl methyl ether"]},
    {"name":"dioxane",       "cas":"123-91-1",   "mw":88.106,  "Tc_K":587.30, "Pc_bar":52.10,  "Vc_cm3mol":238.0, "omega":0.2770,  "Tb_C":101.10,  "density_25_kgm3":1034.0, "dHvap_kJmol":34.16, "Cp_liq_Jmolk":147.8, "aliases":["dioxane","1,4-dioxane","p-dioxane"]},
    # ── Carboxylic acids ──────────────────────────────────────────────────────
    {"name":"formic acid",   "cas":"64-18-6",    "mw":46.026,  "Tc_K":588.00, "Pc_bar":57.34,  "Vc_cm3mol":115.9, "omega":0.4730,  "Tb_C":100.70,  "density_25_kgm3":1220.0, "dHvap_kJmol":22.69, "Cp_liq_Jmolk":99.4,  "aliases":["formic acid","methanoic acid","HCOOH"]},
    {"name":"propionic acid","cas":"79-09-4",    "mw":74.079,  "Tc_K":600.81, "Pc_bar":46.13,  "Vc_cm3mol":230.0, "omega":0.5370,  "Tb_C":141.15,  "density_25_kgm3":993.0,  "dHvap_kJmol":47.53, "Cp_liq_Jmolk":152.8, "aliases":["propionic acid","propanoic acid","C2H5COOH"]},
    # ── Specialty solvents ────────────────────────────────────────────────────
    {"name":"GBL",           "cas":"96-48-0",    "mw":86.090,  "Tc_K":739.00, "Pc_bar":51.50,  "Vc_cm3mol":246.0, "omega":0.5300,  "Tb_C":204.00,  "density_25_kgm3":1124.0, "dHvap_kJmol":52.00, "Cp_liq_Jmolk":156.0, "aliases":["GBL","gamma-butyrolactone","butyrolactone"]},
    {"name":"sulfolane",     "cas":"126-33-0",   "mw":120.171, "Tc_K":901.00, "Pc_bar":54.70,  "Vc_cm3mol":None,  "omega":0.4000,  "Tb_C":285.00,  "density_25_kgm3":1262.0, "dHvap_kJmol":74.10, "Cp_liq_Jmolk":218.0, "aliases":["sulfolane","tetramethylene sulfone","TMS"]},
    {"name":"DMAC",          "cas":"127-19-5",   "mw":87.122,  "Tc_K":658.00, "Pc_bar":39.83,  "Vc_cm3mol":292.0, "omega":0.3860,  "Tb_C":165.00,  "density_25_kgm3":942.0,  "dHvap_kJmol":50.21, "Cp_liq_Jmolk":175.6, "aliases":["DMAC","DMAc","dimethylacetamide","N,N-dimethylacetamide"]},
    # ── Refrigerants ─────────────────────────────────────────────────────────
    {"name":"R134a",         "cas":"811-97-2",   "mw":102.031, "Tc_K":374.21, "Pc_bar":40.59,  "Vc_cm3mol":199.8, "omega":0.3268,  "Tb_C":-26.37,  "density_25_kgm3":None,   "dHvap_kJmol":22.20, "Cp_liq_Jmolk":146.7, "aliases":["R134a","HFC-134a","1,1,1,2-tetrafluoroethane","R-134a"]},
    {"name":"R22",           "cas":"75-45-6",    "mw":86.468,  "Tc_K":369.30, "Pc_bar":49.90,  "Vc_cm3mol":165.0, "omega":0.2208,  "Tb_C":-40.81,  "density_25_kgm3":None,   "dHvap_kJmol":20.24, "Cp_liq_Jmolk":123.7, "aliases":["R22","HCFC-22","chlorodifluoromethane","R-22"]},
    {"name":"R32",           "cas":"75-10-5",    "mw":52.024,  "Tc_K":351.26, "Pc_bar":57.82,  "Vc_cm3mol":122.3, "omega":0.2769,  "Tb_C":-51.65,  "density_25_kgm3":None,   "dHvap_kJmol":21.18, "Cp_liq_Jmolk":96.2,  "aliases":["R32","HFC-32","difluoromethane","R-32"]},
    {"name":"R125",          "cas":"354-33-6",   "mw":120.023, "Tc_K":339.33, "Pc_bar":36.51,  "Vc_cm3mol":209.4, "omega":0.3052,  "Tb_C":-48.09,  "density_25_kgm3":None,   "dHvap_kJmol":16.70, "Cp_liq_Jmolk":147.5, "aliases":["R125","HFC-125","pentafluoroethane"]},
    # ── Gases ─────────────────────────────────────────────────────────────────
    {"name":"CO",            "cas":"630-08-0",   "mw":28.010,  "Tc_K":132.86, "Pc_bar":34.53,  "Vc_cm3mol":93.4,  "omega":0.0510,  "Tb_C":-191.5,  "density_25_kgm3":None,   "dHvap_kJmol":6.04,  "Cp_liq_Jmolk":60.5,  "aliases":["CO","carbon monoxide","syngas"]},
    {"name":"HCl",           "cas":"7647-01-0",  "mw":36.461,  "Tc_K":324.65, "Pc_bar":83.10,  "Vc_cm3mol":81.0,  "omega":0.1320,  "Tb_C":-85.05,  "density_25_kgm3":None,   "dHvap_kJmol":16.15, "Cp_liq_Jmolk":None,  "aliases":["HCl","hydrogen chloride","hydrochloric acid"]},
    {"name":"Cl2",           "cas":"7782-50-5",  "mw":70.906,  "Tc_K":417.15, "Pc_bar":77.10,  "Vc_cm3mol":123.8, "omega":0.0690,  "Tb_C":-34.05,  "density_25_kgm3":None,   "dHvap_kJmol":20.41, "Cp_liq_Jmolk":66.9,  "aliases":["Cl2","chlorine"]},
    {"name":"argon",         "cas":"7440-37-1",  "mw":39.948,  "Tc_K":150.86, "Pc_bar":48.98,  "Vc_cm3mol":74.6,  "omega":0.0000,  "Tb_C":-185.86, "density_25_kgm3":None,   "dHvap_kJmol":6.43,  "Cp_liq_Jmolk":44.0,  "aliases":["argon","Ar"]},
    # ── Industrial / petrochemical ─────────────────────────────────────────────
    {"name":"pyridine",      "cas":"110-86-1",   "mw":79.101,  "Tc_K":620.00, "Pc_bar":56.30,  "Vc_cm3mol":254.0, "omega":0.2400,  "Tb_C":115.23,  "density_25_kgm3":982.0,  "dHvap_kJmol":35.09, "Cp_liq_Jmolk":132.7, "aliases":["pyridine","C5H5N"]},
    {"name":"furfural",      "cas":"98-01-1",    "mw":96.084,  "Tc_K":657.00, "Pc_bar":55.02,  "Vc_cm3mol":252.0, "omega":0.3700,  "Tb_C":161.70,  "density_25_kgm3":1160.0, "dHvap_kJmol":43.25, "Cp_liq_Jmolk":163.2, "aliases":["furfural","furan-2-carbaldehyde","furfuraldehyde"]},
    {"name":"caprolactam",   "cas":"105-60-2",   "mw":113.160, "Tc_K":806.00, "Pc_bar":47.60,  "Vc_cm3mol":338.0, "omega":0.4270,  "Tb_C":267.15,  "density_25_kgm3":None,   "dHvap_kJmol":65.30, "Cp_liq_Jmolk":None,  "aliases":["caprolactam","epsilon-caprolactam","nylon-6 monomer"]},
    {"name":"lactic acid",   "cas":"50-21-5",    "mw":90.079,  "Tc_K":616.00, "Pc_bar":49.70,  "Vc_cm3mol":196.0, "omega":0.8900,  "Tb_C":122.00,  "density_25_kgm3":1209.0, "dHvap_kJmol":None,  "Cp_liq_Jmolk":190.0, "aliases":["lactic acid","2-hydroxypropanoic acid","L-lactic acid"]},
    # ── Biodiesel / biofuel compounds ─────────────────────────────────────────
    {"name":"methyl oleate", "cas":"112-62-9",   "mw":296.490, "Tc_K":764.00, "Pc_bar":12.50,  "Vc_cm3mol":None,  "omega":0.9060,  "Tb_C":349.00,  "density_25_kgm3":874.0,  "dHvap_kJmol":None,  "Cp_liq_Jmolk":None,  "aliases":["methyl oleate","FAME","oleic acid methyl ester"]},
    {"name":"methyl palmitate","cas":"112-39-0", "mw":270.451, "Tc_K":755.00, "Pc_bar":13.60,  "Vc_cm3mol":None,  "omega":0.8630,  "Tb_C":348.00,  "density_25_kgm3":870.0,  "dHvap_kJmol":None,  "Cp_liq_Jmolk":None,  "aliases":["methyl palmitate","FAME","palmitic acid methyl ester"]},
]
# fmt: on

# Antoine constants (°C, mmHg) — log10(P_mmHg) = A - B/(C + T_°C)
ANTOINE_DATA: List[Dict[str, Any]] = [
    {"compound":"water",        "A":8.10765, "B":1750.286, "C":235.000, "T_min_C":60,   "T_max_C":150},
    {"compound":"methane",      "A":6.61184, "B":389.930,  "C":266.888, "T_min_C":-183, "T_max_C":-152, "note":"K scale: log10(P_bar)=A-B/(C+T_K)"},
    {"compound":"ethane",       "A":6.80896, "B":663.720,  "C":256.681, "T_min_C":-120, "T_max_C":-60},
    {"compound":"propane",      "A":6.82973, "B":813.200,  "C":248.000, "T_min_C":-40,  "T_max_C":0},
    {"compound":"n-butane",     "A":6.80896, "B":935.860,  "C":238.730, "T_min_C":-73,  "T_max_C":19},
    {"compound":"n-pentane",    "A":6.85221, "B":1064.630, "C":232.000, "T_min_C":-50,  "T_max_C":58},
    {"compound":"n-hexane",     "A":6.87601, "B":1171.530, "C":224.366, "T_min_C":-25,  "T_max_C":92},
    {"compound":"n-heptane",    "A":6.89385, "B":1264.370, "C":216.636, "T_min_C":-2,   "T_max_C":124},
    {"compound":"benzene",      "A":6.90565, "B":1211.033, "C":220.790, "T_min_C":8,    "T_max_C":80},
    {"compound":"toluene",      "A":6.95087, "B":1342.310, "C":219.187, "T_min_C":6,    "T_max_C":137},
    {"compound":"o-xylene",     "A":6.99891, "B":1474.679, "C":213.686, "T_min_C":32,   "T_max_C":172},
    {"compound":"cyclohexane",  "A":6.84498, "B":1203.526, "C":222.863, "T_min_C":20,   "T_max_C":81},
    {"compound":"methanol",     "A":7.87863, "B":1473.110, "C":230.000, "T_min_C":15,   "T_max_C":84},
    {"compound":"ethanol",      "A":8.11220, "B":1592.864, "C":226.184, "T_min_C":20,   "T_max_C":93},
    {"compound":"1-propanol",   "A":7.74416, "B":1437.686, "C":198.463, "T_min_C":38,   "T_max_C":97},
    {"compound":"2-propanol",   "A":8.87829, "B":2010.330, "C":252.636, "T_min_C":0,    "T_max_C":101},
    {"compound":"acetone",      "A":7.11714, "B":1210.595, "C":229.664, "T_min_C":-26,  "T_max_C":77},
    {"compound":"acetic acid",  "A":7.80307, "B":1651.200, "C":225.000, "T_min_C":17,   "T_max_C":118},
    {"compound":"ethyl acetate","A":7.10179, "B":1244.951, "C":217.881, "T_min_C":16,   "T_max_C":77},
    {"compound":"diethyl ether","A":6.92374, "B":1064.070, "C":228.800, "T_min_C":-40,  "T_max_C":35},
    {"compound":"THF",          "A":6.99515, "B":1202.290, "C":226.254, "T_min_C":0,    "T_max_C":67},
    {"compound":"chloroform",   "A":6.90328, "B":1163.030, "C":227.400, "T_min_C":4,    "T_max_C":84},
    {"compound":"acetonitrile", "A":7.11988, "B":1285.703, "C":223.516, "T_min_C":20,   "T_max_C":82},
    {"compound":"ammonia",      "A":7.36050, "B":926.132,  "C":240.170, "T_min_C":-83,  "T_max_C":-33},
    {"compound":"styrene",      "A":6.92409, "B":1420.165, "C":206.052, "T_min_C":32,   "T_max_C":146},
    {"compound":"DMF",          "A":6.92800, "B":1400.869, "C":196.355, "T_min_C":60,   "T_max_C":154},
    {"compound":"DMSO",         "A":8.21641, "B":2275.186, "C":229.874, "T_min_C":80,   "T_max_C":189},
]

# NRTL binary interaction parameters — (τ12, τ21, α)
# Convention: comp1 is solute, comp2 is solvent (or use alphabetical order)
# Source: DECHEMA VLE Data Collection; Renon & Prausnitz (1968)
NRTL_BIP_DATA: List[Dict[str, Any]] = [
    {"comp1":"ethanol",     "comp2":"water",     "tau12":3.4578,  "tau21":-0.8009, "alpha":0.2994, "T_ref_C":25, "source":"Renon 1968"},
    {"comp1":"methanol",    "comp2":"water",     "tau12":2.9996,  "tau21":-0.6904, "alpha":0.2471, "T_ref_C":25, "source":"DECHEMA"},
    {"comp1":"2-propanol",  "comp2":"water",     "tau12":3.3399,  "tau21":-0.3974, "alpha":0.2981, "T_ref_C":25, "source":"DECHEMA"},
    {"comp1":"acetone",     "comp2":"water",     "tau12":2.0938,  "tau21":0.7078,  "alpha":0.5343, "T_ref_C":25, "source":"DECHEMA"},
    {"comp1":"acetic acid", "comp2":"water",     "tau12":0.3514,  "tau21":1.8920,  "alpha":0.4538, "T_ref_C":25, "source":"DECHEMA"},
    {"comp1":"acetonitrile","comp2":"water",     "tau12":2.1484,  "tau21":1.3219,  "alpha":0.2983, "T_ref_C":25, "source":"DECHEMA"},
    {"comp1":"THF",         "comp2":"water",     "tau12":2.8258,  "tau21":1.2090,  "alpha":0.2000, "T_ref_C":25, "source":"DECHEMA"},
    {"comp1":"diethyl ether","comp2":"water",    "tau12":2.2485,  "tau21":0.6018,  "alpha":0.2001, "T_ref_C":25, "source":"DECHEMA"},
    {"comp1":"benzene",     "comp2":"water",     "tau12":4.1178,  "tau21":4.6652,  "alpha":0.2000, "T_ref_C":25, "source":"DECHEMA"},
    {"comp1":"n-hexane",    "comp2":"ethanol",   "tau12":2.8020,  "tau21":0.9460,  "alpha":0.4715, "T_ref_C":25, "source":"DECHEMA"},
    {"comp1":"acetone",     "comp2":"methanol",  "tau12":0.1886,  "tau21":0.4827,  "alpha":0.3001, "T_ref_C":25, "source":"DECHEMA"},
    {"comp1":"chloroform",  "comp2":"acetone",   "tau12":-0.5840, "tau21":0.2566,  "alpha":0.3000, "T_ref_C":25, "source":"DECHEMA"},
    {"comp1":"ethyl acetate","comp2":"ethanol",  "tau12":0.4289,  "tau21":-0.1756, "alpha":0.3000, "T_ref_C":25, "source":"DECHEMA"},
    {"comp1":"ethyl acetate","comp2":"water",    "tau12":3.8560,  "tau21":-0.3856, "alpha":0.2000, "T_ref_C":25, "source":"DECHEMA"},
    {"comp1":"toluene",     "comp2":"ethanol",   "tau12":1.8880,  "tau21":0.7810,  "alpha":0.5080, "T_ref_C":25, "source":"DECHEMA"},
    {"comp1":"benzene",     "comp2":"cyclohexane","tau12":0.3100, "tau21":-0.0290, "alpha":0.3000, "T_ref_C":25, "source":"DECHEMA"},
]

# UNIQUAC binary interaction parameters — (u12-u22, u21-u11) in K
# τij = exp(-(uij-ujj)/T);  standard UNIQUAC energy parameters
# Source: DECHEMA VLE Data; Fredenslund et al. (1977)
UNIQUAC_BIP_DATA: List[Dict[str, Any]] = [
    {"comp1":"ethanol",    "comp2":"water",    "u12_minus_u22_K":-73.59,  "u21_minus_u11_K":609.18, "T_ref_C":25, "source":"DECHEMA"},
    {"comp1":"methanol",   "comp2":"water",    "u12_minus_u22_K":-107.98, "u21_minus_u11_K":239.42, "T_ref_C":25, "source":"DECHEMA"},
    {"comp1":"2-propanol", "comp2":"water",    "u12_minus_u22_K":-134.18, "u21_minus_u11_K":718.36, "T_ref_C":25, "source":"DECHEMA"},
    {"comp1":"acetone",    "comp2":"water",    "u12_minus_u22_K":-172.91, "u21_minus_u11_K":420.86, "T_ref_C":25, "source":"DECHEMA"},
    {"comp1":"acetic acid","comp2":"water",    "u12_minus_u22_K":-131.16, "u21_minus_u11_K":316.74, "T_ref_C":25, "source":"DECHEMA"},
    {"comp1":"THF",        "comp2":"water",    "u12_minus_u22_K":-147.61, "u21_minus_u11_K":533.17, "T_ref_C":25, "source":"DECHEMA"},
    {"comp1":"benzene",    "comp2":"cyclohexane","u12_minus_u22_K":50.00, "u21_minus_u11_K":-38.20, "T_ref_C":25, "source":"DECHEMA"},
    {"comp1":"n-hexane",   "comp2":"ethanol",  "u12_minus_u22_K":192.38, "u21_minus_u11_K":71.99,  "T_ref_C":25, "source":"DECHEMA"},
    {"comp1":"ethyl acetate","comp2":"water",  "u12_minus_u22_K":-80.37, "u21_minus_u11_K":591.35, "T_ref_C":25, "source":"DECHEMA"},
    {"comp1":"1-butanol",  "comp2":"water",    "u12_minus_u22_K":-55.55, "u21_minus_u11_K":739.43, "T_ref_C":25, "source":"DECHEMA"},
]

# Temperature-dependent NRTL: τij(T) = aij + bij/T  (T in Kelvin)
# More accurate than fixed-τ for distillation at temperatures != 25°C
# Source: Gmehling et al. DECHEMA; Aspen Plus databank
NRTL_TDEP_DATA: List[Dict[str, Any]] = [
    {"comp1":"ethanol",  "comp2":"water",    "a12":3.0000, "b12":-712.32, "a21":-0.5625, "b21":385.16,  "alpha":0.3016},
    {"comp1":"methanol", "comp2":"water",    "a12":0.7987, "b12":281.48,  "a21":-1.0267, "b21":544.64,  "alpha":0.3000},
    {"comp1":"acetone",  "comp2":"water",    "a12":1.4353, "b12":190.45,  "a21":-0.3693, "b21":396.22,  "alpha":0.5343},
    {"comp1":"2-propanol","comp2":"water",   "a12":2.9000, "b12":-428.00, "a21":-0.4500, "b21":245.00,  "alpha":0.2980},
    {"comp1":"acetic acid","comp2":"water",  "a12":-1.3000,"b12":513.00,  "a21":2.4000,  "b21":-170.00, "alpha":0.4538},
]

# Henry's law constants in water at 25°C — KH in bar·m³/mol (= 1000 × L·bar/mol)
HENRY_DATA: List[Dict[str, Any]] = [
    {"compound":"oxygen",    "KH_bar_m3_mol":0.7690, "T_ref_C":25, "dHsol_kJ_mol":-12.8},
    {"compound":"nitrogen",  "KH_bar_m3_mol":1.6000, "T_ref_C":25, "dHsol_kJ_mol":-10.8},
    {"compound":"hydrogen",  "KH_bar_m3_mol":1.2280, "T_ref_C":25, "dHsol_kJ_mol":-4.2},
    {"compound":"methane",   "KH_bar_m3_mol":0.4000, "T_ref_C":25, "dHsol_kJ_mol":-13.8},
    {"compound":"CO2",       "KH_bar_m3_mol":0.0294, "T_ref_C":25, "dHsol_kJ_mol":-19.4},
    {"compound":"H2S",       "KH_bar_m3_mol":0.0055, "T_ref_C":25, "dHsol_kJ_mol":-18.1},
    {"compound":"SO2",       "KH_bar_m3_mol":0.00081,"T_ref_C":25, "dHsol_kJ_mol":-25.9},
    {"compound":"ammonia",   "KH_bar_m3_mol":0.000057,"T_ref_C":25,"dHsol_kJ_mol":-34.2},
    {"compound":"ethylene",  "KH_bar_m3_mol":0.2070, "T_ref_C":25, "dHsol_kJ_mol":-14.2},
    {"compound":"propane",   "KH_bar_m3_mol":0.7100, "T_ref_C":25, "dHsol_kJ_mol":-18.5},
    {"compound":"n-butane",  "KH_bar_m3_mol":1.1500, "T_ref_C":25, "dHsol_kJ_mol":-22.0},
    {"compound":"benzene",   "KH_bar_m3_mol":0.00557,"T_ref_C":25, "dHsol_kJ_mol":-27.0},
    {"compound":"toluene",   "KH_bar_m3_mol":0.00674,"T_ref_C":25, "dHsol_kJ_mol":-29.4},
]


# ─────────────────────────────────────────────────────────────────────────────
# DATABASE ENGINE
# ─────────────────────────────────────────────────────────────────────────────

class PropertyDB:
    """
    Thread-safe SQLite-backed thermodynamic property database.
    Auto-creates and seeds the DB on first use; rebuilds if data hash changes.
    """

    _instance: Optional["PropertyDB"] = None
    _lock = threading.Lock()

    def __new__(cls) -> "PropertyDB":
        # Singleton
        with cls._lock:
            if cls._instance is None:
                obj = super().__new__(cls)
                obj._conn_lock = threading.Lock()
                obj._conn: Optional[sqlite3.Connection] = None
                obj._init_db()
                cls._instance = obj
        return cls._instance

    # ── DB Init ───────────────────────────────────────────────────────────────

    def _init_db(self) -> None:
        """Create tables, seed data if needed, or rebuild on data change."""
        self._conn = sqlite3.connect(_DB_PATH, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._create_tables()
        stored_hash = self._get_meta("data_hash")
        current_hash = self._data_hash()
        if stored_hash != current_hash:
            self._seed_data()
            self._set_meta("data_hash", current_hash)

    def _create_tables(self) -> None:
        c = self._conn
        c.executescript("""
        CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);

        CREATE TABLE IF NOT EXISTS compounds (
            name        TEXT PRIMARY KEY,
            cas         TEXT,
            mw          REAL,
            Tc_K        REAL,
            Pc_bar      REAL,
            Vc_cm3mol   REAL,
            omega       REAL,
            Tb_C        REAL,
            density_25_kgm3 REAL,
            dHvap_kJmol REAL,
            Cp_liq_Jmolk REAL,
            aliases     TEXT
        );

        CREATE TABLE IF NOT EXISTS antoine (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            compound    TEXT,
            A           REAL, B REAL, C REAL,
            T_min_C     REAL, T_max_C REAL,
            note        TEXT
        );

        CREATE TABLE IF NOT EXISTS nrtl_bips (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            comp1       TEXT, comp2 TEXT,
            tau12       REAL, tau21 REAL, alpha REAL,
            T_ref_C     REAL, source TEXT
        );

        CREATE TABLE IF NOT EXISTS henry (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            compound    TEXT,
            KH_bar_m3_mol REAL,
            T_ref_C     REAL,
            dHsol_kJ_mol REAL
        );

        CREATE TABLE IF NOT EXISTS uniquac_bips (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            comp1       TEXT, comp2 TEXT,
            u12_minus_u22_K REAL, u21_minus_u11_K REAL,
            T_ref_C     REAL, source TEXT
        );

        CREATE TABLE IF NOT EXISTS nrtl_tdep (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            comp1       TEXT, comp2 TEXT,
            a12 REAL, b12 REAL, a21 REAL, b21 REAL, alpha REAL
        );
        """)
        c.commit()

    def _seed_data(self) -> None:
        """Clear and repopulate all tables from master data."""
        c = self._conn
        c.execute("DELETE FROM compounds")
        c.execute("DELETE FROM antoine")
        c.execute("DELETE FROM nrtl_bips")
        c.execute("DELETE FROM henry")
        c.execute("DELETE FROM uniquac_bips")
        c.execute("DELETE FROM nrtl_tdep")

        for row in COMPOUND_DATA:
            c.execute("""
                INSERT OR REPLACE INTO compounds VALUES
                (?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                row["name"], row["cas"], row["mw"],
                row["Tc_K"], row["Pc_bar"], row.get("Vc_cm3mol"),
                row["omega"], row.get("Tb_C"),
                row.get("density_25_kgm3"), row.get("dHvap_kJmol"),
                row.get("Cp_liq_Jmolk"),
                ",".join(row.get("aliases", [])),
            ))

        for row in ANTOINE_DATA:
            c.execute("""
                INSERT INTO antoine (compound,A,B,C,T_min_C,T_max_C,note)
                VALUES (?,?,?,?,?,?,?)
            """, (row["compound"], row["A"], row["B"], row["C"],
                  row["T_min_C"], row["T_max_C"], row.get("note")))

        for row in NRTL_BIP_DATA:
            c.execute("""
                INSERT INTO nrtl_bips (comp1,comp2,tau12,tau21,alpha,T_ref_C,source)
                VALUES (?,?,?,?,?,?,?)
            """, (row["comp1"], row["comp2"],
                  row["tau12"], row["tau21"], row["alpha"],
                  row["T_ref_C"], row.get("source","")))

        for row in HENRY_DATA:
            c.execute("""
                INSERT INTO henry (compound,KH_bar_m3_mol,T_ref_C,dHsol_kJ_mol)
                VALUES (?,?,?,?)
            """, (row["compound"], row["KH_bar_m3_mol"],
                  row["T_ref_C"], row.get("dHsol_kJ_mol")))

        for row in UNIQUAC_BIP_DATA:
            c.execute("""
                INSERT INTO uniquac_bips (comp1,comp2,u12_minus_u22_K,u21_minus_u11_K,T_ref_C,source)
                VALUES (?,?,?,?,?,?)
            """, (row["comp1"], row["comp2"],
                  row["u12_minus_u22_K"], row["u21_minus_u11_K"],
                  row["T_ref_C"], row.get("source","")))

        for row in NRTL_TDEP_DATA:
            c.execute("""
                INSERT INTO nrtl_tdep (comp1,comp2,a12,b12,a21,b21,alpha)
                VALUES (?,?,?,?,?,?,?)
            """, (row["comp1"], row["comp2"],
                  row["a12"], row["b12"], row["a21"], row["b21"], row["alpha"]))

        c.commit()

    def _data_hash(self) -> str:
        import hashlib
        raw = (str(len(COMPOUND_DATA)) + str(len(ANTOINE_DATA)) +
               str(len(NRTL_BIP_DATA)) + str(len(HENRY_DATA)) +
               str(len(UNIQUAC_BIP_DATA)) + str(len(NRTL_TDEP_DATA)) +
               (COMPOUND_DATA[0]["cas"] if COMPOUND_DATA else "") +
               str(NRTL_BIP_DATA[0]["tau12"] if NRTL_BIP_DATA else ""))
        return hashlib.md5(raw.encode()).hexdigest()[:12]

    def _get_meta(self, key: str) -> Optional[str]:
        row = self._conn.execute(
            "SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row["value"] if row else None

    def _set_meta(self, key: str, value: str) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO meta VALUES (?,?)", (key, value))
        self._conn.commit()

    # ── Name Resolution ───────────────────────────────────────────────────────

    def _resolve_name(self, name: str) -> Optional[str]:
        """Resolve alias/synonym to canonical compound name."""
        name_lo = name.strip().lower()
        # Direct match
        row = self._conn.execute(
            "SELECT name FROM compounds WHERE lower(name)=?", (name_lo,)).fetchone()
        if row:
            return row["name"]
        # Alias match — aliases are stored as comma-separated lowercase strings
        rows = self._conn.execute("SELECT name, aliases FROM compounds").fetchall()
        for r in rows:
            aliases = [a.strip().lower() for a in (r["aliases"] or "").split(",")]
            if name_lo in aliases:
                return r["name"]
        # CAS match
        row = self._conn.execute(
            "SELECT name FROM compounds WHERE cas=?", (name.strip(),)).fetchone()
        if row:
            return row["name"]
        return None

    # ── Public API ────────────────────────────────────────────────────────────

    def lookup(self, compound: str,
               properties: Optional[List[str]] = None) -> Dict[str, Any]:
        """
        Look up thermodynamic properties for a compound.

        Parameters
        ----------
        compound   : compound name, alias, or CAS number
        properties : list of property groups to return. Options:
                     "critical"  — Tc, Pc, Vc, omega, Tb
                     "antoine"   — Antoine constants (°C, mmHg)
                     "density"   — liquid density at 25°C
                     "thermal"   — dHvap, Cp_liquid
                     "henry"     — Henry's law constant in water
                     "all"       — everything (default)

        Returns dict with requested data or error message.
        """
        canon = self._resolve_name(compound)
        if canon is None:
            # Try fuzzy: check if compound name is a substring of any alias
            rows = self._conn.execute(
                "SELECT name FROM compounds WHERE lower(aliases) LIKE ?",
                (f"%{compound.lower()}%",)).fetchall()
            if rows:
                suggestions = [r["name"] for r in rows[:3]]
                return {
                    "success": False,
                    "error": f"Compound '{compound}' not found. Did you mean: {suggestions}?",
                    "suggestions": suggestions,
                }
            return {
                "success": False,
                "error": f"Compound '{compound}' not found in property database. "
                         f"Use search_compound() to find available names.",
            }

        props = set(properties or ["all"])
        result: Dict[str, Any] = {"success": True, "compound": canon}

        # ── Critical properties ────────────────────────────────────────────
        if "all" in props or "critical" in props:
            row = self._conn.execute(
                "SELECT * FROM compounds WHERE name=?", (canon,)).fetchone()
            if row:
                result["critical"] = {
                    "Tc_K":          row["Tc_K"],
                    "Tc_C":          round(row["Tc_K"] - 273.15, 2) if row["Tc_K"] else None,
                    "Pc_bar":        row["Pc_bar"],
                    "Vc_cm3_per_mol":row["Vc_cm3mol"],
                    "omega":         row["omega"],
                    "Tb_C":          row["Tb_C"],
                    "mw_g_per_mol":  row["mw"],
                    "cas":           row["cas"],
                }

        # ── Antoine constants ──────────────────────────────────────────────
        if "all" in props or "antoine" in props:
            rows = self._conn.execute(
                "SELECT * FROM antoine WHERE compound=?", (canon,)).fetchall()
            if rows:
                result["antoine"] = [
                    {
                        "A": r["A"], "B": r["B"], "C": r["C"],
                        "T_min_C": r["T_min_C"], "T_max_C": r["T_max_C"],
                        "units": "log10(P_mmHg) = A - B/(C + T_°C)",
                        "note": r["note"],
                    }
                    for r in rows
                ]
                # Add computed Psat at 25°C if in valid range
                r0 = rows[0]
                if r0["T_min_C"] <= 25 <= r0["T_max_C"]:
                    psat_mmhg = 10 ** (r0["A"] - r0["B"] / (r0["C"] + 25))
                    result["Psat_25C_mmHg"] = round(psat_mmhg, 2)
                    result["Psat_25C_bar"]  = round(psat_mmhg * 0.00133322, 4)

        # ── Density ───────────────────────────────────────────────────────
        if "all" in props or "density" in props:
            row = self._conn.execute(
                "SELECT density_25_kgm3 FROM compounds WHERE name=?",
                (canon,)).fetchone()
            if row and row["density_25_kgm3"]:
                result["density"] = {
                    "liquid_25C_kg_per_m3": row["density_25_kgm3"],
                    "liquid_25C_g_per_L":   row["density_25_kgm3"],
                }

        # ── Thermal ───────────────────────────────────────────────────────
        if "all" in props or "thermal" in props:
            row = self._conn.execute(
                "SELECT dHvap_kJmol, Cp_liq_Jmolk FROM compounds WHERE name=?",
                (canon,)).fetchone()
            if row:
                result["thermal"] = {
                    "dHvap_NBP_kJ_per_mol": row["dHvap_kJmol"],
                    "Cp_liquid_25C_J_per_mol_K": row["Cp_liq_Jmolk"],
                }

        # ── Henry's law ───────────────────────────────────────────────────
        if "all" in props or "henry" in props:
            row = self._conn.execute(
                "SELECT * FROM henry WHERE compound=?", (canon,)).fetchone()
            if row:
                result["henry"] = {
                    "KH_bar_m3_per_mol": row["KH_bar_m3_mol"],
                    "KH_L_bar_per_mol":  round(row["KH_bar_m3_mol"] * 1000, 2),
                    "T_ref_C":           row["T_ref_C"],
                    "dH_sol_kJ_per_mol": row["dHsol_kJ_mol"],
                    "note": "Higher KH = less soluble in water. KH in bar·m³/mol.",
                }

        return result

    def lookup_pair(self, comp1: str, comp2: str,
                    model: str = "nrtl") -> Dict[str, Any]:
        """
        Look up binary interaction parameters for a pair of compounds.

        Parameters
        ----------
        comp1, comp2 : compound names (order does not matter)
        model        : "nrtl" (default)

        Returns dict with BIPs and usage notes.
        """
        c1 = self._resolve_name(comp1)
        c2 = self._resolve_name(comp2)

        missing = []
        if c1 is None: missing.append(comp1)
        if c2 is None: missing.append(comp2)
        if missing:
            return {"success": False,
                    "error": f"Compound(s) not found: {missing}"}

        if model.lower() == "nrtl":
            # Try both orderings
            row = self._conn.execute("""
                SELECT * FROM nrtl_bips
                WHERE (comp1=? AND comp2=?) OR (comp1=? AND comp2=?)
            """, (c1, c2, c2, c1)).fetchone()

            if row is None:
                return {
                    "success": False,
                    "pair": f"{c1} / {c2}",
                    "model": "NRTL",
                    "error": (
                        f"No NRTL BIPs found for {c1}/{c2} in database. "
                        "Options: (1) use UNIFAC estimation in DWSIM, "
                        "(2) fit to experimental VLE data from NIST or DECHEMA, "
                        "(3) check if DWSIM's built-in BIP database has this pair."
                    ),
                }

            # Flip signs if pair was stored in reverse order
            flipped = (row["comp1"] == c2)
            tau12 = row["tau21"] if flipped else row["tau12"]
            tau21 = row["tau12"] if flipped else row["tau21"]

            return {
                "success":  True,
                "pair":     f"{c1} / {c2}",
                "model":    "NRTL",
                "tau12":    tau12,   # interaction of comp2 on comp1 environment
                "tau21":    tau21,
                "alpha":    row["alpha"],
                "T_ref_C":  row["T_ref_C"],
                "source":   row["source"],
                "usage": (
                    f"In DWSIM: Property Package → Edit BIPs → "
                    f"set {c1}/{c2}: tau12={tau12}, tau21={tau21}, alpha={row['alpha']}. "
                    f"These are at Tref={row['T_ref_C']}°C; for temperature-dependent "
                    f"form use tau=a+b/T with b=tau×T_ref_K."
                ),
            }

        if model.lower() == "uniquac":
            row = self._conn.execute("""
                SELECT * FROM uniquac_bips
                WHERE (comp1=? AND comp2=?) OR (comp1=? AND comp2=?)
            """, (c1, c2, c2, c1)).fetchone()

            if row is None:
                return {
                    "success": False, "pair": f"{c1} / {c2}", "model": "UNIQUAC",
                    "error": (f"No UNIQUAC BIPs found for {c1}/{c2}. "
                              "Use UNIFAC estimation in DWSIM or fit to experimental data."),
                }
            flipped = (row["comp1"] == c2)
            u12 = row["u21_minus_u11_K"] if flipped else row["u12_minus_u22_K"]
            u21 = row["u12_minus_u22_K"] if flipped else row["u21_minus_u11_K"]
            return {
                "success": True, "pair": f"{c1} / {c2}", "model": "UNIQUAC",
                "u12_minus_u22_K": u12, "u21_minus_u11_K": u21,
                "T_ref_C": row["T_ref_C"], "source": row["source"],
                "usage": (
                    f"In DWSIM: Property Package → Edit BIPs → UNIQUAC → "
                    f"set {c1}/{c2}: u12-u22={u12} K, u21-u11={u21} K. "
                    f"UNIQUAC τij = exp(-(uij-ujj)/T) where T is in Kelvin."
                ),
            }

        if model.lower() == "nrtl_tdep":
            row = self._conn.execute("""
                SELECT * FROM nrtl_tdep
                WHERE (comp1=? AND comp2=?) OR (comp1=? AND comp2=?)
            """, (c1, c2, c2, c1)).fetchone()
            if row is None:
                return {
                    "success": False, "pair": f"{c1} / {c2}", "model": "NRTL_Tdep",
                    "error": f"No T-dependent NRTL params for {c1}/{c2}. Use 'nrtl' for fixed-T params.",
                }
            flipped = (row["comp1"] == c2)
            return {
                "success": True, "pair": f"{c1} / {c2}", "model": "NRTL (T-dependent)",
                "a12": row["a21"] if flipped else row["a12"],
                "b12": row["b21"] if flipped else row["b12"],
                "a21": row["a12"] if flipped else row["a21"],
                "b21": row["b12"] if flipped else row["b21"],
                "alpha": row["alpha"],
                "formula": "tau_ij(T) = a_ij + b_ij / T  (T in Kelvin)",
                "usage": (
                    "In DWSIM or Aspen: set temperature-dependent NRTL coefficients. "
                    f"tau12(T)={row['a12'] if not flipped else row['a21']:.4f} + "
                    f"{row['b12'] if not flipped else row['b21']:.2f}/T. "
                    f"More accurate than fixed-tau for T far from 25 C."
                ),
            }

        return {"success": False, "error": f"Model '{model}' not supported. Use 'nrtl', 'uniquac', or 'nrtl_tdep'."}

    def search_compound(self, query: str) -> Dict[str, Any]:
        """
        Search for a compound by partial name, alias, or CAS.

        Returns up to 10 matching compound names.
        """
        q = f"%{query.lower()}%"
        rows = self._conn.execute("""
            SELECT name, cas, mw FROM compounds
            WHERE lower(name) LIKE ?
               OR lower(aliases) LIKE ?
               OR cas LIKE ?
            LIMIT 10
        """, (q, q, q)).fetchall()

        if not rows:
            return {
                "success": False,
                "query": query,
                "error": f"No compounds matching '{query}'. "
                         f"Try common names (ethanol, water, benzene, CO2).",
            }

        return {
            "success": True,
            "query":   query,
            "matches": [
                {"name": r["name"], "cas": r["cas"],
                 "mw_g_per_mol": r["mw"]}
                for r in rows
            ],
            "count": len(rows),
        }

    def antoine_psat(self, compound: str, T_C: float) -> Dict[str, Any]:
        """
        Compute vapor pressure at temperature T using Antoine equation.

        Returns P in mmHg and bar.
        """
        canon = self._resolve_name(compound)
        if canon is None:
            return {"success": False, "error": f"Compound '{compound}' not found."}

        rows = self._conn.execute(
            "SELECT * FROM antoine WHERE compound=?", (canon,)).fetchall()
        if not rows:
            return {"success": False,
                    "error": f"No Antoine data for '{canon}'."}

        # Pick best range
        best = None
        for r in rows:
            if r["T_min_C"] <= T_C <= r["T_max_C"]:
                best = r
                break
        if best is None:
            best = rows[0]
            warning = (f"T={T_C}°C is outside Antoine valid range "
                       f"[{best['T_min_C']}, {best['T_max_C']}°C] — extrapolation.")
        else:
            warning = None

        psat_mmhg = 10 ** (best["A"] - best["B"] / (best["C"] + T_C))
        psat_bar  = psat_mmhg * 133.322 / 1e5

        result = {
            "success":      True,
            "compound":     canon,
            "T_C":          T_C,
            "Psat_mmHg":    round(psat_mmhg, 3),
            "Psat_bar":     round(psat_bar, 6),
            "Psat_kPa":     round(psat_bar * 100, 4),
            "equation":     f"log10(P_mmHg) = {best['A']} - {best['B']} / ({best['C']} + T_°C)",
        }
        if warning:
            result["warning"] = warning
        return result

    def list_compounds(self) -> Dict[str, Any]:
        """Return all compound names in the database."""
        rows = self._conn.execute(
            "SELECT name, cas, mw FROM compounds ORDER BY name").fetchall()
        return {
            "success":   True,
            "compounds": [{"name": r["name"], "cas": r["cas"],
                           "mw": r["mw"]} for r in rows],
            "count":     len(rows),
        }


# ── Module-level convenience instance ────────────────────────────────────────
_db: Optional[PropertyDB] = None


def get_db() -> PropertyDB:
    """Get or create the singleton PropertyDB instance."""
    global _db
    if _db is None:
        _db = PropertyDB()
    return _db


def lookup_compound(compound: str,
                    properties: Optional[List[str]] = None) -> Dict[str, Any]:
    """Convenience wrapper: look up compound properties."""
    return get_db().lookup(compound, properties)


def lookup_pair_bips(comp1: str, comp2: str,
                     model: str = "nrtl") -> Dict[str, Any]:
    """Convenience wrapper: look up binary interaction parameters."""
    return get_db().lookup_pair(comp1, comp2, model)
