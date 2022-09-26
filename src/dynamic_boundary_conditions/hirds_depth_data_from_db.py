# -*- coding: utf-8 -*-
"""
Created on Thu Jan 20 16:36:59 2022.

@authors: pkh35, sli229
"""

import pandas as pd
import pathlib
import logging
import sys
from shapely.geometry import Polygon
from src.dynamic_boundary_conditions import hirds_depth_data_to_db
from src.digitaltwin import setup_environment
from src.dynamic_boundary_conditions import hyetograph

log = logging.getLogger(__name__)
log.setLevel(logging.DEBUG)

formatter = logging.Formatter("%(levelname)s:%(asctime)s:%(name)s:%(message)s")
stream_handler = logging.StreamHandler()
stream_handler.setFormatter(formatter)

log.addHandler(stream_handler)


def filter_for_duration(rain_depth: pd.DataFrame, duration: str) -> pd.DataFrame:
    """
    Used to filter the HIRDS rainfall data for a requested duration.

    Parameters
    ----------
    rain_depth : pd.DataFrame
        HIRDS rainfall data in Pandas Dataframe format.
    duration : str
        Storm duration, i.e. 10m, 20m, 30m, 1h, 2h, 6h, 12h, 24h, 48h, 72h, 96h, 120h, or 'all'.
    """
    if duration != "all":
        rain_depth = rain_depth[["site_id", "rcp", "time_period", "ari", "aep", duration]]
    return rain_depth


def get_each_site_rain_depth_data(
        engine, site_id: str, rcp: float, time_period: str, ari: float, duration: str) -> pd.DataFrame:
    """
    Get the HIRDS rainfall data for the requested site from the database and return the required data in
    Pandas DataFrame format.


    Parameters
    ----------
    engine
        Engine used to connect to the database.
    site_id : str
        HIRDS rainfall site id.
    rcp : float
        There are four different representative concentration pathways (RCPs), and abbreviated as RCP2.6, RCP4.5,
        RCP6.0 and RCP8.5, in order of increasing radiative forcing by greenhouse gases.
    time_period : str
        Rainfall estimates for two future time periods (e.g. 2031-2050 or 2081-2100) for four RCPs.
    ari : float
        Storm average recurrence interval (ARI), i.e. 1.58, 2, 5, 10, 20, 30, 40, 50, 60, 80, 100, or 250.
    duration : str
        Storm duration, i.e. 10m, 20m, 30m, 1h, 2h, 6h, 12h, 24h, 48h, 72h, 96h, 120h, or 'all'.
    """
    if (rcp is None and time_period is not None) or (rcp is not None and time_period is None):
        log.error(
            "Check the arguments of the 'rain_depths_from_db' function. "
            "If rcp is None, time period should be None, and vice-versa.")
        sys.exit()
    elif rcp is not None and time_period is not None:
        query = f"""SELECT * FROM rainfall_depth
        WHERE site_id='{site_id}' AND rcp='{rcp}' AND time_period='{time_period}' AND ari={ari};"""
        rain_depth = pd.read_sql_query(query, engine)
    else:
        query = f"""SELECT * FROM rainfall_depth
        WHERE site_id='{site_id}' AND rcp IS NULL AND time_period IS NULL AND ari={ari};"""
        rain_depth = pd.read_sql_query(query, engine).head(1)
    rain_depth = filter_for_duration(rain_depth, duration)
    return rain_depth


def rain_depths_from_db(
        engine, catchment_polygon: Polygon, rcp: float, time_period: str, ari: float, duration: str) -> pd.DataFrame:
    """
    Get all the rainfall data for the sites within the catchment area and return the required data in
    Pandas DataFrame format.

    Parameters
    ----------
    engine
        Engine used to connect to the database.
    catchment_polygon : Polygon
        Desired catchment area.
    rcp : float
        There are four different representative concentration pathways (RCPs), and abbreviated as RCP2.6, RCP4.5,
        RCP6.0 and RCP8.5, in order of increasing radiative forcing by greenhouse gases.
    time_period : str
        Rainfall estimates for two future time periods (e.g. 2031-2050 or 2081-2100) for four RCPs.
    ari : float
        Storm average recurrence interval (ARI), i.e. 1.58, 2, 5, 10, 20, 30, 40, 50, 60, 80, 100, or 250.
    duration : str
        Storm duration, i.e. 10m, 20m, 30m, 1h, 2h, 6h, 12h, 24h, 48h, 72h, 96h, 120h, or 'all'.
    """
    sites_id_in_catchment = hirds_depth_data_to_db.get_sites_id_in_catchment(engine, catchment_polygon)

    rain_depth_in_catchment = pd.DataFrame()
    for site_id in sites_id_in_catchment:
        rain_depth = get_each_site_rain_depth_data(engine, site_id, rcp, time_period, ari, duration)
        rain_depth_in_catchment = pd.concat([rain_depth_in_catchment, rain_depth], ignore_index=True)
    return rain_depth_in_catchment


def main():
    catchment_file = pathlib.Path(r"src\dynamic_boundary_conditions\catchment_polygon.shp")
    rcp = 2.6
    time_period = "2031-2050"
    ari = 100
    # To get rainfall data for all durations set duration to "all"
    duration = "all"
    engine = setup_environment.get_database()
    catchment_polygon = hyetograph.catchment_area_geometry_info(catchment_file)
    rain_depth_in_catchment = rain_depths_from_db(engine, catchment_polygon, rcp, time_period, ari, duration)
    print(rain_depth_in_catchment)


if __name__ == "__main__":
    main()
