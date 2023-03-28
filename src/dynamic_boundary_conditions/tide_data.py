import logging
import pathlib
from datetime import date, timedelta
from typing import Dict, List, Tuple, Union, Optional

import pandas as pd
import geopandas as gpd
import asyncio
import aiohttp

from src import config
from src.digitaltwin import setup_environment
from src.dynamic_boundary_conditions.tide_enum import DatumType, ApproachType
from src.dynamic_boundary_conditions import tide_query_location

log = logging.getLogger(__name__)
log.setLevel(logging.DEBUG)

formatter = logging.Formatter("%(levelname)s:%(asctime)s:%(name)s:%(message)s")
stream_handler = logging.StreamHandler()
stream_handler.setFormatter(formatter)

log.addHandler(stream_handler)


def get_query_loc_coords_position(query_loc_row: gpd.GeoDataFrame) -> Tuple[float, float, str]:
    query_loc_row = query_loc_row.reset_index(drop=True)
    position = query_loc_row['position'][0]
    query_loc_row = query_loc_row.to_crs(4326)
    query_loc_point = query_loc_row['geometry'][0]
    long, lat = query_loc_point.x, query_loc_point.y
    return lat, long, position


def get_date_ranges(
        start_date: date,
        total_days: int = 365,
        days_per_call: int = 31) -> Dict[date, int]:
    """
    Obtain the start date and the duration, measured in days, for each API call made to retrieve tide data
    for the entire requested period.

    Parameters
    ----------
    start_date : date
        The start date for data collection, which can be in the past or present.
    total_days: int = 365
        The number of days of tide data to collect. The default value is 365 for one year.
    days_per_call : int = 31
        The number of days that can be retrieved in a single API call (between 1 and 31 inclusive).
        The default value is configured to 31, which is the maximum number of days that can be retrieved per API call.
    """
    # Check for invalid arguments
    if total_days < 1:
        raise ValueError(f"total_days is {total_days}, must be at least 1.")
    # Verify that the provided argument meet the query parameter requirements of the Tide API
    if not 1 <= days_per_call <= 31:
        raise ValueError(f"days_per_call is {days_per_call}, must be between 1 and 31 inclusive.")
    # end date for data retrieval
    end_date = start_date + timedelta(days=total_days - 1)
    # Initialize an empty dictionary to store the date ranges
    date_ranges = {}
    # Loop through the date range, adding each chunk of data to the dictionary
    while start_date <= end_date:
        # Determine the start date and number of days for the current chunk
        request_end_date = min(end_date, start_date + timedelta(days=days_per_call - 1))
        number_of_days = (request_end_date - start_date).days + 1
        # Add the current chunk to the date_ranges dictionary
        date_ranges[start_date] = number_of_days
        # Move the start date forward for the next chunk
        start_date += timedelta(days=number_of_days)
    return date_ranges


def gen_api_query_param_list(
        api_key: str,
        lat: Union[int, float],
        long: Union[int, float],
        datum: DatumType,
        date_ranges: Dict[date, int],
        interval: Optional[int] = None) -> List[Dict[str, Union[str, int]]]:
    """
    Generate a list of api query parameters used to retrieve high and low tide data for the entire requested period.

    Parameters
    ----------
    api_key : str
        NIWA api key (https://developer.niwa.co.nz/).
    lat : Union[int, float]
        Latitude range -29 to -53 (- eg: -30.876).
    long : Union[int, float]
        Longitude range 160 to 180 and -175 to -180 (- eg: -175.543).
    datum : DatumType
        Datum used. LAT: Lowest astronomical tide; MSL: Mean sea level.
    date_ranges : Dict[date, int]
        Dictionary of start date and number of days for each API call that need to be made to retrieve
        high and low tide data for the entire requested period.
    interval: Optional[int] = None
        Output time interval in minutes, range from 10 to 1440 minutes (1 day).
        Omit to get only high and low tide times.
    """
    # Verify that the provided arguments meet the query parameter requirements of the Tide API
    if not (-53 <= lat <= -29):
        raise ValueError(f"latitude is {lat}, must range from -29 to -53.")
    if not ((160 <= long <= 180) or (-180 <= long <= -175)):
        raise ValueError(f"longitude is {long}, must range from 160 to 180 or from -175 to -180.")
    if interval is not None and not (10 <= interval <= 1440):
        raise ValueError(f"interval is {interval}, must range from 10 to 1440.")
    # Create a list of api query parameters for all 'date_ranges'
    query_param_list = []
    for start_date, number_of_days in date_ranges.items():
        query_param = {
            "apikey": api_key,
            "lat": str(lat),
            "long": str(long),
            "numberOfDays": number_of_days,
            "startDate": start_date.isoformat(),
            "datum": datum.value
        }
        if interval is not None:
            query_param["interval"] = interval
        query_param_list.append(query_param)
    return query_param_list


async def fetch_tide_data(
        session: aiohttp.ClientSession,
        query_param: Dict[str, Union[str, int]],
        url: str = 'https://api.niwa.co.nz/tides/data'):
    """
    Fetch high and low tide data for a single query parameter.

    Parameters
    ----------
    session : aiohttp.ClientSession
        An aiohttp ClientSession object.
    query_param : Dict[str, Union[str, int]]
        The query parameters used to retrieve high and low tide data for a specific location and time period.
    url: str = 'https://api.niwa.co.nz/tides/data'
        Tide API HTTP request url.
    """
    async with session.get(url, params=query_param) as resp:
        resp_dict = await resp.json()
        tide_df = pd.DataFrame(resp_dict['values'])
        tide_df.insert(loc=0, column='datum', value=resp_dict['metadata']['datum'])
        tide_df.insert(loc=1, column='latitude', value=resp_dict['metadata']['latitude'])
        tide_df.insert(loc=2, column='longitude', value=resp_dict['metadata']['longitude'])
        geometry = gpd.points_from_xy(tide_df['longitude'], tide_df['latitude'])
        tide_df = gpd.GeoDataFrame(tide_df, geometry=geometry, crs=4326)
        return tide_df


async def fetch_tide_data_for_requested_period(
        query_param_list: List[Dict[str, Union[str, int]]],
        url: str = 'https://api.niwa.co.nz/tides/data'):
    """
    Iterate over the list of API query parameters to fetch high and low tide data for the requested period.

    Parameters
    ----------
    query_param_list : List[Dict[str, Union[str, int]]]
        A list of api query parameters used to retrieve high and low tide data for the entire requested period.
    url: str = 'https://api.niwa.co.nz/tides/data'
        Tide API HTTP request url.
    """
    tasks = []
    async with aiohttp.ClientSession() as session:
        # Create a list of tasks that fetch tide data for each query parameter
        for query_param in query_param_list:
            tasks.append(fetch_tide_data(session, query_param=query_param, url=url))
        # Wait for all tasks to complete and concatenate the results into a single DataFrame
        query_results = await asyncio.gather(*tasks, return_exceptions=True)
        tide_data = pd.concat(query_results)
    return tide_data


def convert_to_nz_timezone(tide_data_utc: pd.DataFrame) -> pd.DataFrame:
    """
    Convert the time column in the initially retrieved tide data for the requested period from UTC to NZ timezone.

    Parameters
    ----------
    tide_data_utc : pd.DataFrame
        The original tide data obtained for the requested period with the time column expressed in UTC.
    """
    tide_data = tide_data_utc.copy()
    tide_data['time'] = pd.to_datetime(tide_data['time'])
    tide_data['time'] = tide_data['time'].dt.tz_convert(tz='Pacific/Auckland')
    return tide_data


def fetch_tide_data_from_niwa(
        tide_query_loc: gpd.GeoDataFrame,
        api_key: str,
        datum: DatumType,
        start_date: date,
        total_days: int = 365,
        interval: Optional[int] = None) -> pd.DataFrame:
    """
    Retrieve tide data from NIWA for the requested time period using the centroid coordinates of the catchment area.

    Parameters
    ----------
    tide_query_loc : gpd.GeoDataFrame
        GeoPandas dataframe containing the query coordinate and its position.
    api_key : str
        NIWA api key (https://developer.niwa.co.nz/).
    datum : DatumType
        Datum used. LAT: Lowest astronomical tide; MSL: Mean sea level.
    start_date : date
        The start date for data collection, which can be in the past or present.
    total_days: int = 365
        The number of days of tide data to collect. The default value is 365 for one year.
    interval: Optional[int] = None
        Output time interval in minutes, range from 10 to 1440 minutes (1 day).
        Omit to get only high and low tide times.
    """
    # Get the date_ranges (i.e. start date and the duration used for each API call)
    date_ranges = get_date_ranges(start_date, total_days)
    # Get the tide data for each of the tide query location
    tide_data_utc = pd.DataFrame()
    for index, row in tide_query_loc.iterrows():
        query_loc_row = gpd.GeoDataFrame([row], crs=tide_query_loc.crs)
        lat, long, position = get_query_loc_coords_position(query_loc_row)
        # Get the list of api query parameters used to retrieve high and low tide data
        query_param_list = gen_api_query_param_list(api_key, lat, long, datum, date_ranges, interval)
        # Iterate over the list of API query parameters to fetch high and low tide data for the requested period
        query_loc_tide = asyncio.run(fetch_tide_data_for_requested_period(query_param_list))
        query_loc_tide['position'] = position
        tide_data_utc = pd.concat([tide_data_utc, query_loc_tide])
    tide_data_utc = tide_data_utc.reset_index(drop=True)
    # Convert time column from UTC to NZ timezone.
    tide_data = convert_to_nz_timezone(tide_data_utc)
    # Filter out data
    end_date = start_date + timedelta(days=total_days - 1)
    tide_data = tide_data.loc[tide_data['time'].dt.date <= end_date]
    # Rename columns
    new_col_names = {'time': 'datetime_nz', 'value': 'tide_metres'}
    tide_data.rename(columns=new_col_names, inplace=True)
    return tide_data


def get_highest_tide_datetime(tide_data: pd.DataFrame) -> pd.Timestamp:
    """
    Get the datetime of the most recent highest tide that occurred within the requested time period.

    Parameters
    ----------
    tide_data : pd.DataFrame
        The original tide data obtained for the requested period with the time column expressed in NZ timezone
        (converted from UTC).
    """
    # Find the highest tide value
    max_tide_value = tide_data['tide_metres'].max()
    highest_tide = tide_data[tide_data['tide_metres'] == max_tide_value]
    # Get the datetime of the most recent highest tide
    highest_tide = highest_tide.sort_values(by=['datetime_nz'], ascending=False).reset_index(drop=True)
    highest_tide_datetime = highest_tide.iloc[0]['datetime_nz']
    log.info(f"The highest tide datetime is: {highest_tide_datetime}")
    return highest_tide_datetime


def get_highest_tide_datetime_span(
        highest_tide_datetime: pd.Timestamp,
        tide_length_mins: int) -> Tuple[pd.Timestamp, pd.Timestamp]:
    half_tide_length_mins = tide_length_mins / 2
    half_tide_length_timedelta = timedelta(minutes=half_tide_length_mins)
    start_datetime = highest_tide_datetime - half_tide_length_timedelta
    end_datetime = highest_tide_datetime + half_tide_length_timedelta
    return start_datetime, end_datetime


def get_highest_tide_date_span(start_datetime: pd.Timestamp, end_datetime: pd.Timestamp) -> Tuple[date, int]:
    start_date = start_datetime.date()
    end_date = end_datetime.date()
    total_days = (end_date - start_date).days + 1
    return start_date, total_days


def fetch_highest_tide_side_data(
        tide_data: pd.DataFrame,
        tide_length_mins: int,
        api_key: str,
        datum: DatumType,
        interval: Optional[int] = None):
    highest_tide_datetime = get_highest_tide_datetime(tide_data)
    start_datetime, end_datetime = get_highest_tide_datetime_span(highest_tide_datetime, tide_length_mins)
    start_date, total_days = get_highest_tide_date_span(start_datetime, end_datetime)
    # get unique pairs of coordinates
    highest_tide_query_loc = tide_data[['position', 'geometry']].drop_duplicates().reset_index(drop=True)
    highest_tide_data = fetch_tide_data_from_niwa(
        highest_tide_query_loc, api_key, datum, start_date, total_days, interval)
    data_around_highest_tide = highest_tide_data.loc[
        highest_tide_data['datetime_nz'].between(start_datetime, end_datetime)]
    data_around_highest_tide = data_around_highest_tide.reset_index(drop=True)
    return data_around_highest_tide


def get_tide_data(
        approach: ApproachType,
        api_key: str,
        datum: DatumType,
        tide_query_loc: gpd.GeoDataFrame,
        start_date: date,
        total_days: Optional[int] = None,
        tide_length_mins: Optional[int] = None,
        interval: Optional[int] = None):
    if approach == ApproachType.KING_TIDE:
        if tide_length_mins is None:
            raise ValueError("tide_length_mins parameter must be provided for ApproachType.KING_TIDE")
        tide_data = fetch_tide_data_from_niwa(tide_query_loc, api_key, datum, start_date, total_days=365, interval=None)
        data_around_highest_tide = fetch_highest_tide_side_data(tide_data, tide_length_mins, api_key, datum, interval)
        return data_around_highest_tide
    else:
        if total_days is None:
            raise ValueError("total_days parameter must be provided for ApproachType.PERIOD_TIDE")
        tide_data = fetch_tide_data_from_niwa(tide_query_loc, api_key, datum, start_date, total_days, interval)
        return tide_data


def main():
    # Connect to the database
    engine = setup_environment.get_database()
    # Catchment polygon
    catchment_file = pathlib.Path(r"selected_polygon.geojson")
    catchment_area = tide_query_location.get_catchment_area(catchment_file)
    # Get NIWA api key
    niwa_api_key = config.get_env_variable("NIWA_API_KEY")
    # Get regions (clipped) that intersect with the catchment area from the database
    regions_clipped = tide_query_location.get_regions_clipped_from_db(engine, catchment_area)
    tide_query_loc = tide_query_location.get_tide_query_locations(
        engine, catchment_area, regions_clipped, distance_km=1)
    # Specify the datum query parameter
    datum = DatumType.LAT
    # Get tide data
    tide_data = get_tide_data(
        approach=ApproachType.KING_TIDE,
        api_key=niwa_api_key,
        datum=datum,
        tide_query_loc=tide_query_loc,
        start_date=date(2023, 1, 23),
        total_days=3,  # used for PERIOD_TIDE
        tide_length_mins=2880,  # used for KING_TIDE
        interval=10)
    print(tide_data)


if __name__ == "__main__":
    main()
