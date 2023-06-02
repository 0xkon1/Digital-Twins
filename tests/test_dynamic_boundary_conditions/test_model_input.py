import unittest
from unittest.mock import patch
import pathlib

import geopandas as gpd
import pandas as pd
import numpy as np
from shapely.geometry import Polygon

from src.dynamic_boundary_conditions import rainfall_model_input


class RainfallModelInputTest(unittest.TestCase):
    """Tests for rainfall_model_input.py."""

    @staticmethod
    def get_catchment_polygon(filepath: str) -> Polygon:
        """
        Get the catchment boundary geometry (polygon).

        Parameters
        ----------
        filepath
            The file path of the catchment polygon GeoJSON data file.
        """
        catchment_file = pathlib.Path(filepath)
        catchment = gpd.read_file(catchment_file)
        catchment = catchment.to_crs(4326)
        catchment_polygon = catchment["geometry"][0]
        return catchment_polygon

    @classmethod
    def setUpClass(cls):
        """Get all relevant data used for testing."""
        cls.selected_polygon = cls.get_catchment_polygon(
            r"tests/test_dynamic_boundary_conditions/data/selected_polygon.geojson")
        cls.sites_in_catchment = gpd.read_file(
            r"tests/test_dynamic_boundary_conditions/data/sites_in_catchment.geojson")
        cls.intersections = gpd.read_file(
            r"tests/test_dynamic_boundary_conditions/data/intersections.geojson")
        cls.sites_coverage = gpd.read_file(
            r"tests/test_dynamic_boundary_conditions/data/sites_coverage.geojson")
        cls.hyetograph_data_alt_block = pd.read_csv(
            r"tests/test_dynamic_boundary_conditions/data/hyetograph_data_alt_block.txt")
        cls.hyetograph_data_chicago = pd.read_csv(
            r"tests/test_dynamic_boundary_conditions/data/hyetograph_data_chicago.txt")

    def test_sites_voronoi_intersect_catchment_within_catchment(self):
        """Test to ensure returned intersections (overlapped areas) are each within the catchment area."""
        intersections = rainfall_model_input.sites_voronoi_intersect_catchment(self.sites_in_catchment, self.selected_polygon)
        self.assertTrue(intersections.within(self.selected_polygon.buffer(1 / 1e13)).unique())

    def test_sites_voronoi_intersect_catchment_area_size(self):
        """Test to ensure the area size of each returned intersection (overlapped areas) is not greater than
        its original area size."""
        intersections = rainfall_model_input.sites_voronoi_intersect_catchment(self.sites_in_catchment, self.selected_polygon)
        org_area_sizes = self.sites_in_catchment.to_crs(3857).area / 1e6
        intersection_area_sizes = intersections.to_crs(3857).area / 1e6
        result = intersection_area_sizes.gt(org_area_sizes).any()
        self.assertFalse(result)

    @patch("src.dynamic_boundary_conditions.model_input.sites_voronoi_intersect_catchment")
    def test_sites_coverage_in_catchment_correct_area_percent(self, mock_intersections):
        """Test to ensure the percentage of area covered by each rainfall site inside the catchment area has
        been calculated correctly and sums up to 1."""
        mock_intersections.return_value = self.intersections.copy()
        sites_coverage = rainfall_model_input.sites_coverage_in_catchment(
            sites_in_catchment=gpd.GeoDataFrame(),
            catchment_polygon=Polygon())

        sites_area = (self.intersections.to_crs(3857).area / 1e6)
        sites_area_percent = sites_area / sites_area.sum()
        pd.testing.assert_series_equal(sites_area_percent, sites_coverage["area_percent"], check_names=False)
        self.assertEqual(1, sites_coverage["area_percent"].sum())

    def test_mean_catchment_rainfall_correct_calculation(self):
        """Test to ensure the returned data have been calculated correctly (ignore rounding)."""
        site_area_percent = self.sites_coverage[["site_id", "area_percent"]]
        hyetograph_data_list = [self.hyetograph_data_alt_block, self.hyetograph_data_chicago]
        for hyetograph_data in hyetograph_data_list:
            mean_catchment_rain = rainfall_model_input.mean_catchment_rainfall(hyetograph_data, self.sites_coverage)
            for row_index in range(len(hyetograph_data)):
                row_hyeto_data = hyetograph_data.iloc[row_index, :-3]
                row_hyeto_data = row_hyeto_data.to_frame(name="rain_intensity_mmhr").reset_index(names="site_id")
                row_hyeto_data = pd.merge(row_hyeto_data, site_area_percent, how="left", on="site_id")
                row_mean_catchment_rain = (row_hyeto_data["rain_intensity_mmhr"] * row_hyeto_data["area_percent"]).sum()
                self.assertAlmostEqual(
                    row_mean_catchment_rain, mean_catchment_rain["rain_intensity_mmhr"].iloc[row_index])

    def test_mean_catchment_rainfall_correct_rows(self):
        """Test to ensure the returned data have correct number of rows."""
        hyetograph_data_list = [self.hyetograph_data_alt_block, self.hyetograph_data_chicago]
        for hyetograph_data in hyetograph_data_list:
            mean_catchment_rain = rainfall_model_input.mean_catchment_rainfall(hyetograph_data, self.sites_coverage)
            self.assertEqual(len(hyetograph_data), len(mean_catchment_rain))

    def test_create_rain_data_cube_correct_intensity_in_data_cube(self):
        """Test to ensure the returned rain data cube has correct intensity for each time slice."""
        hyetograph_data_list = [self.hyetograph_data_alt_block, self.hyetograph_data_chicago]
        for hyetograph_data in hyetograph_data_list:
            rain_data_cube = rainfall_model_input.create_rain_data_cube(hyetograph_data, self.sites_coverage)
            for row_index in range(len(hyetograph_data)):
                row_unique_intensity = np.sort(hyetograph_data.iloc[row_index, :-3].unique()).tolist()
                time_slice = rain_data_cube.sel(time=hyetograph_data.iloc[row_index]["seconds"])
                time_slice_intensity = time_slice.data_vars["rain_intensity_mmhr"]
                time_slice_unique_intensity = np.unique(time_slice_intensity)[np.unique(time_slice_intensity) != 0]
                time_slice_unique_intensity = np.sort(time_slice_unique_intensity).tolist()
                self.assertEqual(row_unique_intensity, time_slice_unique_intensity)


if __name__ == "__main__":
    unittest.main()
