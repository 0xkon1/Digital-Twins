# -*- coding: utf-8 -*-
"""
Created on Fri Jan 14 14:05:35 2022

@author: pkh35, sli229
"""
import logging
import json
import os
import pathlib
import subprocess
from datetime import datetime
from typing import Tuple

import xarray as xr
from geoalchemy2 import Geometry
from sqlalchemy import Column, Integer, String
from sqlalchemy import DateTime
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

from src import config
from src.digitaltwin import setup_environment
from src.dynamic_boundary_conditions.rainfall_enum import RainInputType
from src.lidar import dem_metadata_in_db

log = logging.getLogger(__name__)
log.setLevel(logging.DEBUG)

formatter = logging.Formatter("%(levelname)s:%(asctime)s:%(name)s:%(message)s")
stream_handler = logging.StreamHandler()
stream_handler.setFormatter(formatter)

log.addHandler(stream_handler)

Base = declarative_base()


def valid_bg_flood_model(bg_flood_dir: pathlib.Path):
    """Check if the flood_model path exists."""
    if bg_flood_dir.exists():
        return bg_flood_dir
    raise FileNotFoundError(f"BG-flood model at '{bg_flood_dir}' not found.")


def process_tide_input_files(
        tide_input_file_path: pathlib.Path) -> Tuple[str, str]:
    tide_position = tide_input_file_path.stem.split('_')[0]
    tide_file = tide_input_file_path.name
    return tide_position, tide_file


def process_river_input_files(
        river_input_file_path: pathlib.Path) -> str:
    file_name_parts = river_input_file_path.stem.split('_')
    file_name = file_name_parts[0] + river_input_file_path.suffix
    extents = ','.join(file_name_parts[1:])
    river = f"{file_name},{extents}"
    new_file_path = river_input_file_path.with_name(file_name)
    river_input_file_path.rename(new_file_path)
    return river


def bg_model_inputs(
        bg_flood_dir: pathlib.Path,
        dem_path,
        model_output_dir: pathlib.Path,
        catchment_boundary,
        resolution,
        end_time,
        output_timestep,
        rain_input_type: RainInputType,
        mask=15,
        gpu_device=0,
        small_nc=0
):
    """Set parameters to run the flood model.
    mask is used for visualising all the values larger than 15.
    If we are using the gpu then set to 0 (if no gpu type -1).
    smallnc = 0 means Level of refinement to apply to resolution based on the
    adaptive resolution trigger
    """
    now = datetime.now()
    dt_string = now.strftime("%Y_%m_%d_%H_%M_%S")
    with xr.open_dataset(dem_path) as file_nc:
        max_temp_xr = file_nc
    keys = max_temp_xr.data_vars.keys()
    elev_var = list(keys)[1]
    rainfall = "rain_forcing.txt" if rain_input_type == RainInputType.UNIFORM else "rain_forcing.nc?rain_intensity_mmhr"
    # BG Flood is not capable of creating output directories, so we must ensure this is done before running the model.
    if not os.path.isdir(model_output_dir):
        os.makedirs(model_output_dir)
    outfile = (model_output_dir / f"output_{dt_string}.nc").as_posix()
    valid_bg_flood_dir = valid_bg_flood_model(bg_flood_dir)
    param_file_path = valid_bg_flood_dir / "BG_param.txt"
    with open(param_file_path, "w+") as param_file:
        param_file.write(f"topo = {dem_path}?{elev_var};\n"
                         f"gpudevice = {gpu_device};\n"
                         f"mask = {mask};\n"
                         f"dx = {resolution};\n"
                         f"smallnc = {small_nc};\n"
                         f"outputtimestep = {output_timestep};\n"
                         f"endtime = {end_time};\n"
                         f"rain = {rainfall};\n"
                         f"outvars = h, hmax, zb, zs, u, v;\n"
                         f"outfile = {outfile};\n")
        for tide_input_file_path in valid_bg_flood_dir.glob('*_bnd.txt'):
            tide_position, tide_file = process_tide_input_files(tide_input_file_path)
            param_file.write(f"{tide_position} = {tide_file},2;\n")
        for river_input_file_path in valid_bg_flood_dir.glob('river[0-9]*_*.txt'):
            river = process_river_input_files(river_input_file_path)
            param_file.write(f"river = {river};\n")
    model_output_to_db(outfile, catchment_boundary)


def model_output_to_db(outfile, catchment_boundary):
    """Store metadata of model output in database."""
    engine = setup_environment.get_database()
    BGDEM.__table__.create(bind=engine, checkfirst=True)
    filepath = outfile
    filename = os.path.basename(filepath)
    geometry = str(catchment_boundary["geometry"][0])
    flood_dem = BGDEM(filepath=filepath, Filename=filename, geometry=geometry)
    Session = sessionmaker(bind=engine)
    session = Session()
    session.add(flood_dem)
    session.commit()


class BGDEM(Base):
    """Class used to create model_output table in the database."""

    __tablename__ = "model_output"
    unique_id = Column(Integer, primary_key=True, autoincrement=True)
    filepath = Column(String)
    Filename = Column(String)
    access_date = Column(DateTime, default=datetime.now())
    geometry = Column(Geometry("POLYGON"))


def find_latest_model_output(output_dir: pathlib.Path):
    """
    Find the latest BG-Flood model output.

    Parameters
    ----------
    output_dir : pathlib.Path
        BG-Flood model output directory.
    """
    list_of_files = list(output_dir.glob("*.nc"))
    if not len(list_of_files):
        raise ValueError(f"Missing BG-Flood Model output in: {output_dir}")
    latest_file = max(list_of_files, key=os.path.getctime)
    return latest_file


def add_crs_to_latest_model_output(model_output_dir: pathlib.Path):
    """
    Add CRS to the latest BG-Flood Model Output.

    Parameters
    ----------
    model_output_dir : pathlib.Path
        BG-Flood model output directory.
    """
    latest_file = find_latest_model_output(model_output_dir)
    with xr.open_dataset(latest_file, decode_coords="all") as latest_output:
        latest_output.load()
        if latest_output.rio.crs is None:
            latest_output.rio.write_crs("epsg:2193", inplace=True)
    latest_output.to_netcdf(latest_file)


def run_model(
        bg_flood_dir: pathlib.Path,
        model_output_dir: pathlib.Path,
        instructions,
        catchment_boundary,
        resolution,
        end_time,
        output_timestep,
        rain_input_type: RainInputType,
        engine
):
    """Call the functions."""
    dem_path = dem_metadata_in_db.get_dem_path(instructions, engine)
    bg_model_inputs(
        bg_flood_dir,
        dem_path,
        model_output_dir,
        catchment_boundary,
        resolution,
        end_time,
        output_timestep,
        rain_input_type
    )
    os.chdir(bg_flood_dir)
    subprocess.run([bg_flood_dir / "BG_flood.exe"], check=True)
    add_crs_to_latest_model_output(model_output_dir)


def read_and_fill_instructions():
    """Reads instruction file and adds keys and uses selected_polygon.geojson as catchment_boundary"""
    linz_api_key = config.get_env_variable("LINZ_API_KEY")
    instruction_file = pathlib.Path("src/flood_model/instructions_geofabrics.json")
    with open(instruction_file, "r") as file_pointer:
        instructions = json.load(file_pointer)
    instructions["instructions"]["apis"]["vector"]["linz"]["key"] = linz_api_key
    instructions["instructions"]["data_paths"]["catchment_boundary"] = (
                pathlib.Path(os.getcwd()) / pathlib.Path("selected_polygon.geojson")).as_posix()
    instructions["instructions"]["data_paths"]["local_cache"] = instructions["instructions"]["data_paths"][
        "local_cache"].format(data_dir=config.get_env_variable("DATA_DIR"))
    return instructions


def main():
    engine = setup_environment.get_database()
    # BG-Flood Model directory
    bg_flood_dir = config.get_env_variable("FLOOD_MODEL_DIR", cast_to=pathlib.Path)
    # BG-Flood Model output directory
    model_output_dir = config.get_env_variable("DATA_DIR_MODEL_OUTPUT", cast_to=pathlib.Path)
    instructions = read_and_fill_instructions()
    catchment_boundary = dem_metadata_in_db.get_catchment_boundary()
    resolution = instructions["instructions"]["output"]["grid_params"]["resolution"]
    # Saving the outputs after each `outputtimestep` seconds
    output_timestep = 100.0
    # Saving the outputs till `endtime` number of seconds (or the output after `endtime` seconds
    # is the last one)
    end_time = 900.0
    run_model(
        bg_flood_dir=bg_flood_dir,
        model_output_dir=model_output_dir,
        instructions=instructions,
        catchment_boundary=catchment_boundary,
        resolution=resolution,
        end_time=end_time,
        output_timestep=output_timestep,
        rain_input_type=RainInputType.UNIFORM,
        engine=engine
    )


if __name__ == "__main__":
    main()
