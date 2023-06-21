# -*- coding: utf-8 -*-
"""
@Date: 17/06/2023
@Author: sli229
"""

import geopandas as gpd

from src.digitaltwin import setup_environment, instructions_records_to_db, data_to_db
from src.digitaltwin.utils import get_catchment_area


def main(selected_polygon_gdf: gpd.GeoDataFrame) -> None:
    # Connect to the database
    engine = setup_environment.get_database()
    # Get catchment area
    catchment_area = get_catchment_area(selected_polygon_gdf, to_crs=2193)
    # Store 'instructions_run' records in the 'geospatial_layers' table in the database.
    instructions_records_to_db.store_instructions_records_to_db(engine)
    # Store geospatial layers data in the database
    data_to_db.store_geospatial_layers_data_to_db(engine, catchment_area)
    # Store user log information in the database
    data_to_db.user_log_info_to_db(engine, catchment_area)


if __name__ == "__main__":
    sample_polygon = gpd.GeoDataFrame.from_file("selected_polygon.geojson")
    main(sample_polygon)
