"""
Author: Benjamin Arroquia Cuadros
Project: Calculate data from copernicus
Path and global variables
'./data/interim/municipios_geobiomet.gpkg'
"""
import pathlib
import os
current = pathlib.Path().resolve()

DB_PATH = os.path.join(current, "data/GeoBiomet_remove.db")
DOWNLOAD_FOLDERS = os.path.join(current, "data/download")
PROCESSED_FOLDERS = os.path.join(current, "data/processed")
POL_PROVINCES = os.path.join(current, "data/interim/municipios_geobiomet.gpkg")
LAYER_PROVINCES = 'provincias_pob'
LAYER_POPULATION = 'pob_mun2016'
LAYER_CLUSTERS = 'clusters_methods_provinces'
CLUSTER_COLUM_NAME = 'POB16'
RASTER_CHECK_PATH = os.path.join(current, "data/raster_crop_script.tif")
COPERNICUS_CATALOGE = 'reanalysis-cerra-single-levels'
GPKG = os.path.join(current, "data/spatial_clustering.gpkg")

DC_DOWNLOAD_COPERNICUS = {
        'variable': [
            '2m_relative_humidity', '2m_temperature',
            'surface_pressure'
        ],
        "level_type": "surface_or_atmosphere",
        "data_type": ["reanalysis"],
        "product_type": "analysis",
        "year": ["2003"],
        "month": ["01"],
        'day': [
            '01', '02', '03',
            '04', '05', '06',
            '07', '08', '09',
            '10', '11', '12',
            '13', '14', '15',
            '16', '17', '18',
            '19', '20', '21',
            '22', '23', '24',
            '25', '26', '27',
            '28', '29', '30',
            '31',
        ],
        'time': [
            '00:00', '06:00', '12:00',
            '18:00',
        ],
        'format': 'grib',
}

# '/media/barroquia/data/Documents/upv/phd/2022_python/copernicus/data/raster_crop_script.tif'