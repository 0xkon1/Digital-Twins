# -*- coding: utf-8 -*-
"""
@Description: This script provides utility functions for logging configuration and geospatial data manipulation.
@Author: sli229
"""

import logging
import pathlib
import inspect
import warnings
from enum import IntEnum

import geopandas as gpd
from sqlalchemy.engine import Engine

log = logging.getLogger(__name__)


class LogLevel(IntEnum):
    """
    Enum class representing different logging levels mapped to their corresponding numeric values from the
    logging library.

    Attributes
    ----------
    CRITICAL : int
        The critical logging level. Corresponds to logging.CRITICAL (50).
    ERROR : int
        The error logging level. Corresponds to logging.ERROR (40).
    WARNING : int
        The warning logging level. Corresponds to logging.WARNING (30).
    INFO : int
        The info logging level. Corresponds to logging.INFO (20).
    DEBUG : int
        The debug logging level. Corresponds to logging.DEBUG (10).
    NOTSET : int
        The not-set logging level. Corresponds to logging.NOTSET (0).
    """
    CRITICAL = logging.CRITICAL
    ERROR = logging.ERROR
    WARNING = logging.WARNING
    INFO = logging.INFO
    DEBUG = logging.DEBUG
    NOTSET = logging.NOTSET


def setup_logging(log_level: LogLevel = LogLevel.DEBUG) -> None:
    """
    Configures the root logger with the specified log level and formats, captures warnings, and excludes specific
    loggers from propagating their messages to the root logger. Additionally, logs a debug message indicating the
    execution of the function in the script.

    Parameters
    ----------
    log_level : int, optional
        The log level to set for the root logger. Defaults to LogLevel.DEBUG.
        The available logging levels and their corresponding numeric values are:
        - LogLevel.CRITICAL (50)
        - LogLevel.ERROR (40)
        - LogLevel.WARNING (30)
        - LogLevel.INFO (20)
        - LogLevel.DEBUG (10)
        - LogLevel.NOTSET (0)

    Returns
    -------
    None
        This function does not return any value.
    """
    # Define the logging format and date format
    logging_format = "%(asctime)s | %(levelname)-8s | %(name)-60s %(lineno)4d | %(funcName)-40s | %(message)s"
    date_format = "%Y-%m-%d %H:%M:%S"
    # Create and configure the root logger with the specified log level and formats
    logging.basicConfig(level=log_level, format=logging_format, datefmt=date_format, force=True)
    # Enable capturing Python warnings and redirect them to the logging system
    logging.captureWarnings(True)
    # Suppress (ignore) Python warnings from appearing in the console
    warnings.simplefilter("ignore")
    # List of loggers to prevent messages from reaching the root logger
    loggers_to_exclude = ["urllib3", "fiona", "botocore", "pyproj", "asyncio", "rasterio"]
    # Iterate through the loggers to exclude
    for logger_name in loggers_to_exclude:
        # Get the logger instance for each name in the list
        logger = logging.getLogger(logger_name)
        # Disable log message propagation from these loggers to the root logger
        logger.propagate = False

    # Get the calling stack frame (the previous frame in the call stack)
    stack_frame = inspect.currentframe().f_back
    # Extract the name of the script file (without the path) where the function is being executed
    script_name = pathlib.Path(stack_frame.f_globals["__file__"]).name
    # Extract the name of the function currently being executed
    function_name = stack_frame.f_code.co_name
    # Log a debug message indicating the execution of the function in the script
    log.debug(f"Executing {function_name}() in {script_name}")


def get_catchment_area(catchment_area: gpd.GeoDataFrame, to_crs: int = 2193) -> gpd.GeoDataFrame:
    """
    Convert the coordinate reference system (CRS) of the catchment area GeoDataFrame to the specified CRS.

    Parameters
    ----------
    catchment_area : gpd.GeoDataFrame
        The GeoDataFrame representing the catchment area.
    to_crs : int, optional
        Coordinate Reference System (CRS) code to convert the catchment area to. Default is 2193.

    Returns
    -------
    gpd.GeoDataFrame
        The catchment area GeoDataFrame with the transformed CRS.
    """
    return catchment_area.to_crs(to_crs)


def get_nz_boundary(engine: Engine, to_crs: int = 2193) -> gpd.GeoDataFrame:
    """
    Get the boundary of New Zealand in the specified Coordinate Reference System (CRS).

    Parameters
    ----------
    engine : Engine
        The engine used to connect to the database.
    to_crs : int, optional
        Coordinate Reference System (CRS) code to which the boundary will be converted. Default is 2193.

    Returns
    -------
    gpd.GeoDataFrame
        A GeoDataFrame representing the boundary of New Zealand in the specified CRS.
    """
    # Query the 'region_geometry' table from the database using the provided engine
    query = "SELECT * FROM region_geometry;"
    region_geometry = gpd.GeoDataFrame.from_postgis(query, engine, geom_col="geometry")
    # Dissolve and explode the geometries to get the boundary of New Zealand
    nz_boundary = region_geometry.dissolve(aggfunc="sum").explode(index_parts=True).reset_index(level=0, drop=True)
    # Calculate the area of each geometry and sort them in descending order
    nz_boundary["geometry_area"] = nz_boundary["geometry"].area
    nz_boundary = nz_boundary.sort_values(by="geometry_area", ascending=False).head(1)
    # Convert to the desired coordinate reference system (CRS)
    nz_boundary = nz_boundary.to_crs(to_crs)
    return nz_boundary
