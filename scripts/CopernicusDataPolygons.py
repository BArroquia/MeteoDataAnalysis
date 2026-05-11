"""
Author: Benjamin Arroquia Cuadros
01/11/2022

Module to clip and extract statistics from raster file.
Dependencies: osgeo, numpy, pandas

Script workflow:
1. Download data every month from 2016 to 2018
2. Process data: calculate doa and store Zonal Statistics

Workflow of data processing:
1. Read dowload folder
2. If exist '*.grib' file process
3. Get all metadata of raster: time and variables
4. Process data: DOA
    4.1 Get all bands and create dict with dates and 3 vars to calculate DOA
    4.2 Crop all raster to create DOA obj inside vectorial layer
    4.3 Create DOAs band
    4.4 Iterate over polygon entities and gather zonal statistics
    4.5 Insert in db
    4.6 Create gtiff of doas
5. Process data: copernicus variables
6. Copy file and 



ProvPolygons transform coordinates into Lambert Conical,

Data raster from Copernicus has to be allocated in a folder './data/download'.
All data in dowload folder is processed, deleted and copied in processed.

DOA error in humidity variable from copernicus.
Correct using (h+273.15) in doa function.

DOA is calculated croping full raster using bbox from polygon layer.
This aims to reduce time processing and be efficient.
Then stats are calculated over single entities in polygon layer (provinces).


Use case: get pixel values stats about every polygon
        in a shapefile that intersects with a raster layer 
        
layer_prov = ProvPolygons(shape_path='./limprovinciaetrs89/provincias_doa.shp')    
    
layer_prov.clear()
doa_raster.clear()
"""

import numpy as np
from osgeo import gdal, osr, ogr
import sys, os
import pandas as pd
import sqlite3
import settings
import glob
from datetime import datetime
import sqlalchemy
from sqlalchemy import Table, Column, Integer, Float, String, MetaData, DateTime
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.ext.declarative import declarative_base
import pyproj
import math
import shutil
import cdsapi

# MODELS
meta = MetaData()

Base = declarative_base()

class Download(Base):
    __tablename__ = "download"
    name = Column('name', String, primary_key=True)
    time = Column('time', DateTime(timezone=False),
           default=datetime.utcnow, primary_key=True) 
    path = Column('path', String)
    status = Column('status', String)
    def __repr__(self):
        return f"Download(name={self.name!r}, time={self.time!r}), status={self.status!r})"

    
class StatsLayer(Base):
    __tablename__ = "stats_cop"
    variable = Column('variable', String, primary_key=True)
    time = Column('time', DateTime(timezone=False), 
                     default=datetime.utcnow, primary_key=True)
    entity = Column('entity', String) 
    cod_entity = Column('cod_entity', String, primary_key=True) 
    shape = Column('shape', Float)
    mean = Column('mean', Float) 
    median = Column('median', Float) 
    stdd = Column('stdd', Float)
    variance = Column('variance', Float) 
    maxi = Column('maxi', Float) 
    mini = Column('mini', Float) 
    def __repr__(self):
        return f"StatsCop(variable={self.variable!r}, entity={self.entity!r}, mean={self.mean!r})"
    

# CLASS
class ProvPolygons:
    """
    Class to calculate data of provinces
    Create a dict with NAMEUNIT and NATCODE fields.
    Input layer must have this fields.
    Input: shapefile of provinces
    """
    def __init__(self, shape_path):
        """
        Input: shapefile with provinces
        """
        driver = ogr.GetDriverByName("ESRI Shapefile")
        self.shp = driver.Open(shape_path, 0)
        self.lyr = self.shp.GetLayer()
        self.define_geotransform()
        self.ls_idfeat = list(range(self.lyr.GetFeatureCount()))
        self.dc_feat = dict(zip(self.ls_idfeat, [{} for i in self.ls_idfeat]))
        self.ly_x_min = None
        self.ly_x_max = None
        self.ly_y_min = None
        self.ly_y_max = None
        self.convexhull_from_layer()
        self.read_features()

        
    def read_features(self):
        # Get metadata from provincias and bbox
        for FID in self.ls_idfeat:
            feat = self.lyr.GetFeature(FID)
            self.dc_feat[FID]['entity'] = feat.GetField("NAMEUNIT")
            self.dc_feat[FID]['cod_entity'] = feat.GetField("NATCODE")[4:6]
            self.create_dict_features(feat, FID)
    
    def define_geotransform(self, from_epsg=4326):
        source = pyproj.CRS.from_epsg(from_epsg)
        ecmwf_lcc = pyproj.CRS.from_proj4(
            '+proj=lcc +lat_0=50 +lat_1=50 +lat_2=50 +lon_0=8 +x_0=0 +y_0=0 +R=6371229 +units=m +no_defs'
        )
        self.transform_to_ecmwf = pyproj.Transformer.from_crs(source, ecmwf_lcc, always_xy=True)
        
    def get_bbox_layer(self):
        dc_bbox = {
            'ly_x_min': self.ly_x_min,
            'ly_x_max': self.ly_x_max,
            'ly_y_min': self.ly_y_min,
            'ly_y_max': self.ly_y_max
        }
        return dc_bbox

    def create_dict_features(self, feat, fid):
        geom = feat.GetGeometryRef()
        # create list of point to get bbox
        pointsX = []; pointsY = []

        if (geom.GetGeometryName() == 'MULTIPOLYGON'):
            count = 0
            for polygon in geom:
                geomInner = geom.GetGeometryRef(count)
                ring = geomInner.GetGeometryRef(0)
                numpoints = ring.GetPointCount()
                for p in range(numpoints):
                        lon, lat, z = ring.GetPoint(p)
                        x, y = self.transform_to_ecmwf.transform(lon, lat)
                        pointsX.append(x)
                        pointsY.append(y)
                count += 1
        elif (geom.GetGeometryName() == 'POLYGON'):
            ring = geom.GetGeometryRef(0)
            numpoints = ring.GetPointCount()
            for p in range(numpoints):
                    lon, lat, z = ring.GetPoint(p)
                    x, y = self.transform_to_ecmwf.transform(lon, lat)
                    pointsX.append(x)
                    pointsY.append(y)
        self.dc_feat[fid]['xmin'] = min(pointsX)
        self.dc_feat[fid]['xmax'] = max(pointsX)
        self.dc_feat[fid]['ymin'] = min(pointsY)
        self.dc_feat[fid]['ymax'] = max(pointsY)
    
    def get_list_fields_name(self):
        schema = []
        ldefn = self.lyr.GetLayerDefn()
        for n in range(ldefn.GetFieldCount()):
            fdefn = ldefn.GetFieldDefn(n)
            schema.append(fdefn.name)
        print(schema)
        
    def convexhull_from_layer(self):
        """
        Calculate bbox to define mask area before calculate doa.
        The aim is reduce computacional cost of calculate doa.
        """
        # Collect all Geometry
        geomcol = ogr.Geometry(ogr.wkbGeometryCollection)
        for feature in self.lyr:
            geomcol.AddGeometry(feature.GetGeometryRef())
        pointsX = []; pointsY = []
        # Calculate convex hull
        convexhull = geomcol.ConvexHull()
        ring = convexhull.GetGeometryRef(0)
        numpoints = ring.GetPointCount()
        for p in range(numpoints):
                lon, lat, z = ring.GetPoint(p)
                x, y = self.transform_to_ecmwf.transform(lon, lat)
                pointsX.append(x)
                pointsY.append(y)
        self.ly_x_min = min(pointsX)
        self.ly_x_max = max(pointsX)
        self.ly_y_min = min(pointsY)
        self.ly_y_max = max(pointsY)

        
    def select_by_atribute(self, value):
        # Value of nameunit or privince name
        self.lyr.SetAttributeFilter('"NAMEUNIT"=\'%s\'' % (value))
    
    def get_layer(self):
        return self.lyr
    
    def clear(self):
        self.shp = None

class DOA:
    """
    Class to create memfile of doa
    Create a new raster with multiple bands.
    Raster in same BBox defined x_lon_min, y_lat_max, xcount, ycount
    To get zonal stats iterate over bands
    """
    def __init__(self, pixelWidth, pixelHeight, x_lon_min, y_lat_max, xcount, ycount, raster_proj, metadata, bands):
        # Mem file
        self.target_doa = gdal.GetDriverByName('MEM').Create('', xcount, ycount, bands, gdal.GDT_Float32)
        self.target_doa.SetGeoTransform((
            x_lon_min, pixelWidth, 0,
            y_lat_max, 0, pixelHeight,
        ))
        self.band_number = 1
        self.target_doa.SetProjection(raster_proj)
        metadata['GRIB_COMMENT'] = 'Density of Oxygen in Air (DOA)'
        metadata['GRIB_UNIT'] = '[o²]'
        metadata['GRIB_SHORT_NAME'] = 'DOA'
        metadata['GRIB_ELEMENT'] = 'DOA'
        metadata['GRIB_DISCIPLINE'] = 'Biometeorology'
        self.metadata_doa = metadata
        transform = self.target_doa.GetGeoTransform()
        self.number_bands = self.target_doa.RasterCount
        self.xOrigin = transform[0]
        self.yOrigin = transform[3]
        self.pixelWidth = transform[1]
        self.pixelHeight = transform[5]
        
    def append_new_band(self, temp, press, humi, metadata):
        matx_res = self.calculate_doa(temp, press, humi)
        self.target_doa.GetRasterBand(self.band_number).WriteArray(matx_res)
        meta_band = metadata
        meta_band['GRIB_IDS'] = metadata['GRIB_IDS']
        meta_band['GRIB_REF_TIME'] = metadata['GRIB_REF_TIME']
        meta_band['GRIB_VALID_TIME'] = metadata['GRIB_VALID_TIME']
        date = dict([i.split('=') for i in metadata['GRIB_IDS'].split(' ')])['REF_TIME']
        meta_band['REF_TIME'] = date
        meta_band['GRIB_COMMENT'] = 'Density of Oxygen in Air (DOA)'
        meta_band['GRIB_UNIT'] = '[o²]'
        meta_band['GRIB_SHORT_NAME'] = 'DOA'
        meta_band['GRIB_ELEMENT'] = 'DOA'
        meta_band['GRIB_DISCIPLINE'] = 'Biometeorology'
        self.target_doa.GetRasterBand(self.band_number).SetMetadata(meta_band)
        self.band_number = self.band_number + 1
        return self.band_number -1
        
    def calculate_doa(self, temp, press, humi):
        """
        Calculate DOA index.
        Needed a n-matrix dimensioned and ordered: pressure, humidity, temperature
        :param matx: numpy matrix. 0-pressure, 1-humidity, 2-temperature. [pressure, humidity, temperature]
        :return: numpy matx_res
        """
        if (temp.shape == press.shape) and (humi.shape == press.shape):
            r, c = temp.shape
            ls_matrix = []
            for i in range(0, r):
                ls_row = []
                for j in range(0, c):
                    try:
                        p = press[i, j]
                        h = humi[i, j]+273.15
                        t = temp[i, j]
                        # relative humidity
                        hpa = p / 100.0
                        humidity_rel = (h) / 100.0
                        h6 = t + 35 * math.log(humidity_rel)
                        TVA = 6.112 * math.exp((17.7 * h6) / (h6 + 243.5))
                        doa_value = round((80.51 * hpa) / (t + 273) * (1 - TVA / hpa), 2)
                        ls_row.append(doa_value)
                    except Exception as e:
                        ls_row.append(0.0)
                        print("DOA: ", e, humidity_rel, h, p, t)
                ls_matrix.append(ls_row)
                matx_res = np.array(ls_matrix)
        else:
            matx_res = None
        return matx_res

    def get_doa_matrix(self, band):
        return self.target_doa.GetRasterBand(band).ReadAsArray().astype(float)
    
    def get_raster_params(self, band):
        str_format = "%Y-%m-%d %H:%M:%S"
        band_sel = self.target_doa.GetRasterBand(band)
        var_name = band_sel.GetMetadata()['GRIB_COMMENT']
        time_sec = band_sel.GetMetadata()['GRIB_REF_TIME']
        time_sec = datetime.fromtimestamp(int(time_sec.strip().split()[0])).strftime(str_format)
        return time_sec, var_name

    def print_geotransform(self):
        print('Doa geoT: ', self.target_doa.GetGeoTransform())
        
    def create_tiff():
        # save in file doa matrix
        pass
    
    def save_raster_to_check(self, file_name):
        out_raster = f"{settings.PROCESSED_FOLDERS}/doa_{file_name}.grib"
        driver = gdal.GetDriverByName('GRIB')
        if os.path.exists(out_raster):
            os.remove(out_raster)
        dst = driver.CreateCopy(out_raster, self.target_doa, 0) 
        dst = None
        driver_ds = None
        
    def get_band_by_datetime(self, datetime):
        band_num = None
        for i in range(1, self.band+1):
            btime = self.target_doa.GetRasterBand(i).GetMetadata()['GRIB_REF_TIME']
            if btime == datetime:
                print("localiza banda")
                band_num = i
        return band_num
    
    def crop_raster_from_polygon(self, lyr, dc_entities, band_num):
        """
        Calculate new mem raster data using geometry
        Parameters:
            lyr, dc_entities, datetime, band_num
        Return:
            None
        """
        # Specify offset and rows and columns to read
        xoff = abs(int((self.xOrigin - dc_entities['xmin'])/self.pixelWidth))
        yoff = int((self.yOrigin - dc_entities['ymax'])/self.pixelWidth)
        xcount = int((dc_entities['xmax'] - dc_entities['xmin'])/self.pixelWidth)
        ycount = abs(int((dc_entities['ymax'] - dc_entities['ymin'])/self.pixelWidth))
        # Establece el origen correcto para el plot
        x_lon_min = xoff * self.pixelWidth + self.xOrigin
        y_lat_max = (yoff *self.pixelWidth - self.yOrigin) * -1

        # Create memory target raster
        self.target_ds = gdal.GetDriverByName('MEM').Create('', xcount, ycount, 1, gdal.GDT_Float32)
        self.target_ds.SetGeoTransform((
            x_lon_min, self.pixelWidth, 0,
            y_lat_max, 0, self.pixelHeight,
        ))
        self.target_ds.SetProjection(self.target_doa.GetProjection())
        # rasterize
        gdal.RasterizeLayer(self.target_ds, [1], lyr)
        # Read raster as arrays
        try:
            dataraster = self.target_doa.GetRasterBand(band_num).ReadAsArray(xoff, yoff, xcount, ycount).astype(float)
            bandmask = self.target_ds.GetRasterBand(1)
            datamask = bandmask.ReadAsArray(0, 0, xcount, ycount).astype(float)
            # Mask zone of raster
            zoneraster = np.ma.masked_array(dataraster,  np.logical_not(datamask))
            zoneraster = np.ma.filled(zoneraster, np.nan)
            # self.target_ds.GetRasterBand(1).WriteArray(np.ma.filled(zoneraster, np.nan))
            # self.target_ds.GetRasterBand(1).SetNoDataValue(0.0)
        except Exception as e:
            print("poligonize: ", e)
        matrix_poligonized = np.ma.filled(zoneraster, np.nan)
        return matrix_poligonized
            
    def get_stats(self, lyr, dc_entities, band_number):
        """
        Calculate tuple of statistics creating a new raster layer.
        Parameters:
            feat (dict): data about geometry related with the mask
            layer (ogr): layer with geometry selected to create a mask
            datetime (int): number band
        Return:
            stats (tuple): shape, mean, median, std, var, max, min
        """
        npdata = self.crop_raster_from_polygon(lyr, dc_entities, band_number)
        ar_no_nans = npdata.flatten()[~np.isnan(npdata.flatten())]
        stats = ar_no_nans.shape[0], np.mean(ar_no_nans),\
                np.median(ar_no_nans), np.std(ar_no_nans),\
                np.var(ar_no_nans), np.max(ar_no_nans),\
                np.min(ar_no_nans)
        return list(map(round_float, map(float, stats)))
    
    def get_stats_provinces(self, layer_prov):
        """
        Loop over bands and create a lis of zonal statistics
        """
        ls_rows = []
        for i in range(1, self.number_bands+1):
            fecha = datetime.strptime(self.get_raster_params(i)[0], '%Y-%m-%d %H:%M:%S')
            variable_atm = self.get_raster_params(i)[1]
            for k, feat in layer_prov.dc_feat.items():
                layer_prov.select_by_atribute(feat['entity'])
                lsstats = self.get_stats(layer_prov.get_layer(), feat, i)
                # # formating insert in table
                row = [variable_atm, fecha, feat['entity'], 
                       feat['cod_entity']] + lsstats
                ls_rows.append(row)
        return ls_rows

            

class RasterExtraction:
    """
    Class to get stats from netcdf files
    """
    ls_vars_doa = ['Relative humidity [%]', 
                       'Temperature [C]', 'Pressure [Pa]']
    def __init__(self, grib_file):
        self.path = grib_file
        # raster data
        self.raster = gdal.Open(grib_file)
        # Get raster georeference info
        transform = self.raster.GetGeoTransform()
        self.number_bands = self.raster.RasterCount
        self.xOrigin = transform[0]
        self.yOrigin = transform[3]
        self.pixelWidth = transform[1]
        self.pixelHeight = transform[5]
        self.band = None
        self.bandraster = None
        self.ly_xoff = None
        self.ly_yoff = None
        self.ly_xcount = None
        self.ly_ycount = None
        self.doa_obj = None

    
    def get_raster_params(self, band):
        str_format = "%Y-%m-%d %H:%M:%S"
        band_sel = self.raster.GetRasterBand(band)
        var_name = band_sel.GetMetadata()['GRIB_COMMENT']
        time_sec = band_sel.GetMetadata()['GRIB_REF_TIME']
        time_sec = datetime.fromtimestamp(int(time_sec.strip().split()[0])).strftime(str_format)
        return time_sec, var_name

        
    def has_all_doa_variables(self, dc_vars):
        result = True
        for k, v in dc_vars.items():
            if k not in self.ls_vars_doa:
                result = False
        return result
    
    def process_bbox_to_crop(self, dc_bbox):
        # Define vars to crop from vectotrial data 
        # This should be abstract for any projection.
        # here only valid for spain.
        self.ly_xoff = abs(int((self.xOrigin - dc_bbox['ly_x_min'])/self.pixelWidth))
        self.ly_yoff = int((self.yOrigin - dc_bbox['ly_y_max'])/self.pixelWidth)
        self.ly_xcount = int((dc_bbox['ly_x_max'] - dc_bbox['ly_x_min'])/self.pixelWidth)
        self.ly_ycount = abs(int((dc_bbox['ly_y_max'] - dc_bbox['ly_y_min'])/self.pixelWidth))
        # Establece el origen correcto sin desplazamiento del raster
        self.x_lon_min = self.ly_xoff * self.pixelWidth + self.xOrigin
        self.y_lat_max = (self.ly_yoff *self.pixelWidth - self.yOrigin) * -1
    
    def get_array_band_crop(self, band_number, crop=False):
        band = self.raster.GetRasterBand(band_number)
        # check if process doa with all raster or crop is fast
        if crop:
            if (self.ly_ycount == None) and (self.ly_xoff == None):
                self.process_bbox_to_crop(crop)
            array_band = band.ReadAsArray(self.ly_xoff, self.ly_yoff, 
                                    self.ly_xcount, self.ly_ycount).astype(float)
        else:
            array_band = band.ReadAsArray().astype(float)
        return array_band
    
    def get_list_with_variables(self, crop_raster=False):
        dc_time = {}
        ls_doas = []
        for i in range(1, self.number_bands+1):
            # construct a dict with all raster grouped by time
            band_sel = self.raster.GetRasterBand(i)
            time, var_name = self.get_raster_params(band=i)
            if time in dc_time:
                dc_time[time][var_name] = i
            else:
                dc_time[time] = {var_name: i}  
        for k, v in dc_time.items():
            # check if exists 3 variables to calculate doa
            if len(v) == 3 and self.has_all_doa_variables(dc_vars=v):
                humi = self.get_array_band_crop(v[self.ls_vars_doa[0]], crop_raster)
                temp = self.get_array_band_crop(v[self.ls_vars_doa[1]], crop_raster)
                press = self.get_array_band_crop(v[self.ls_vars_doa[2]], crop_raster)
                dc_vars = {
                    'time': k,
                    'humidity': humi,
                    'temperature': temp,
                    'pressure': press,
                    'band': v[self.ls_vars_doa[0]]
                }
                ls_doas.append(dc_vars)
        return ls_doas
    
    def print_stats(self, ar_no_nans):
        stats = ar_no_nans.shape[0], np.mean(ar_no_nans),\
        np.median(ar_no_nans), np.max(ar_no_nans),\
        np.min(ar_no_nans)
        print(stats)
    
    def create_doa_from_raster(self, layer):
        # Two way to create DOA and compare processing time
        # ls_doas = self.get_list_with_variables(crop_raster=False)
        ls_doas = self.get_list_with_variables(crop_raster=layer.get_bbox_layer())
        ls_time = []
        if self.doa_obj is None:
            metadata_r = self.raster.GetRasterBand(1).GetMetadata()
            self.doa_obj = DOA(pixelWidth=self.pixelWidth, pixelHeight=self.pixelHeight,
                          x_lon_min=self.x_lon_min, y_lat_max=self.y_lat_max, 
                          xcount=self.ly_xcount, ycount=self.ly_ycount, 
                          raster_proj=self.raster.GetProjection(),
                          metadata=metadata_r, bands=len(ls_doas))
        for i, vars_doa in enumerate(ls_doas):
            # create doas by time and append in object
            metadata_r = self.raster.GetRasterBand(vars_doa['band']).GetMetadata()
            b = self.doa_obj.append_new_band(temp=vars_doa['temperature'], 
                               press=vars_doa['pressure'], 
                               humi=vars_doa['humidity'],
                               metadata=metadata_r)
            ls_doas[i]['doa_band'] = b
            time, var = self.doa_obj.get_raster_params(b)
            ls_time.append(time)
            for k, feat in layer.dc_feat.items():
                layer.select_by_atribute(feat['entity'])
        # uncomment this and create a new file tiff
        min_t, max_t = min(ls_time), max(ls_time)
        # file_doa = f"DOA_{min_t.strftime('%Y/%m/%d')}_{max_t.strftime('%Y/%m/%d')}"
        file_doa = f"DOA_{min_t}_{max_t}"
        self.doa_obj.save_raster_to_check(file_name=file_doa)
        
    def crop_raster_from_polygon(self, lyr, dc_entities, band_num):
        """
        Calculate new mem raster data using geometry
        Parameters:
            lyr, dc_entities, datetime, band_num
        Return:
            None
        """
        # Specify offset and rows and columns to read
        xoff = abs(int((self.xOrigin - dc_entities['xmin'])/self.pixelWidth))
        yoff = int((self.yOrigin - dc_entities['ymax'])/self.pixelWidth)
        xcount = int((dc_entities['xmax'] - dc_entities['xmin'])/self.pixelWidth)
        ycount = abs(int((dc_entities['ymax'] - dc_entities['ymin'])/self.pixelWidth))
        # Establece el origen correcto para el plot
        x_lon_min = xoff * self.pixelWidth + self.xOrigin
        y_lat_max = (yoff *self.pixelWidth - self.yOrigin) * -1

        # Create memory target raster
        self.target_ds = gdal.GetDriverByName('MEM').Create('', xcount, ycount, 1, gdal.GDT_Float32)
        self.target_ds.SetGeoTransform((
            x_lon_min, self.pixelWidth, 0,
            y_lat_max, 0, self.pixelHeight,
        ))
        self.target_ds.SetProjection(self.raster.GetProjection())
        # rasterize
        gdal.RasterizeLayer(self.target_ds, [1], lyr)
        # Read raster as arrays
        try:
            dataraster = self.raster.GetRasterBand(band_num).ReadAsArray(xoff, yoff, xcount, ycount).astype(float)
            bandmask = self.target_ds.GetRasterBand(1)
            datamask = bandmask.ReadAsArray(0, 0, xcount, ycount).astype(float)
            # Mask zone of raster
            zoneraster = np.ma.masked_array(dataraster,  np.logical_not(datamask))
            if 'Relative humidity' in self.raster.GetRasterBand(band_num).GetMetadata()['GRIB_COMMENT']:
                # band of humidity is wrong
                zoneraster = np.ma.filled(zoneraster, np.nan) + 273.15
            else:
                zoneraster = np.ma.filled(zoneraster, np.nan)
            self.target_ds.GetRasterBand(1).WriteArray(np.ma.filled(zoneraster, np.nan))
            self.target_ds.GetRasterBand(1).SetNoDataValue(0.0)
        except Exception as e:
            print("poligonize: ", e)
        return np.ma.filled(zoneraster, np.nan)
        
    def set_band_raster(self, band):
        if band != self.band:
            self.band = band
            self.bandraster = self.raster.GetRasterBand(band)
            
    def get_stats(self, lyr, dc_entities, band_number):
        """
        Calculate tuple of statistics creating a new raster layer.
        Parameters:
            dc_entities (dict): data about geometry related with the mask
            lyr (ogr): layer with geometry selected to create a mask
            band_number (int): number band
        Return:
            stats (tuple): shape, mean, median, std, var, max, min
        """
        npdata = self.crop_raster_from_polygon(lyr, dc_entities, band_number)
        ar_no_nans = npdata.flatten()[~np.isnan(npdata.flatten())]
        stats = ar_no_nans.shape[0], np.mean(ar_no_nans),\
                np.median(ar_no_nans), np.std(ar_no_nans),\
                np.var(ar_no_nans), np.max(ar_no_nans),\
                np.min(ar_no_nans)
        return list(map(round_float, map(float, stats)))
    
    def get_stats_provinces(self, layer_prov):
        """
        Loop over bands and create a lis of zonal statistics
        """
        ls_rows = []
        for i in range(1, self.number_bands+1):
            fecha = datetime.strptime(self.get_raster_params(i)[0], '%Y-%m-%d %H:%M:%S')
            variable_atm = self.get_raster_params(i)[1]
            for k, feat in layer_prov.dc_feat.items():
                layer_prov.select_by_atribute(feat['entity'])
                lsstats = self.get_stats(layer_prov.get_layer(), feat, i)
                # # formating insert in table
                row = [variable_atm, fecha, feat['entity'], 
                       feat['cod_entity']] + lsstats
                ls_rows.append(row)
        return ls_rows
        
    def get_doa(self):
        return self.doa_obj
            
    def clear(self):
        self.raster = None


class SqlDB:
    def __init__(self, path, meta, Base):
        self.path_db = path
        self.meta = meta
        self.Base = Base
        self.create_db()
        # self.session = sessionmaker(bind=self.engine)

    def create_db(self):
        self.engine = sqlalchemy.create_engine(self.path_db)
        self.Base.metadata.create_all(self.engine)
            
    def inser_to_table(self, table, ls_data):
        """
        :param table: class of table
        :param ls_data: list of values
        """
        cols = [c.key for c in table.__table__.columns]
        row = dict(zip(cols, ls_data))
        with Session(self.engine) as session:
            session.add(Download(**row))
            session.commit()
        
    def inser_many_to_table(self, table, ls_data):
        """
        :param table: class of table
        :param ls_data: list of lists of values
        """
        ls_rows = []
        cols = [c.key for c in table.__table__.columns]
        for data in ls_data:
            row = dict(zip(cols, data))
            ls_rows.append(table(**row))
        with Session(self.engine) as session:
            session.add_all(ls_rows)
            session.commit()
    
    def update_table_download(self, table, name, time, value):
        with Session(self.engine) as session:
            down = session.query(table).filter(table.name == name, 
                                               table.time == time).one()
            down.status = value
            session.commit()
        
# FUNCTIONS
def round_float(i):
    return round(i, 3)

def read_download_files():
    txtfiles = []
    folder = os.path.join(settings.DOWNLOAD_FOLDERS, "*.grib")
    for file in glob.glob(folder):
        txtfiles.append(file)
    return txtfiles

def create_folders():
    ls_folders = [
        './data/download',
        './data/failures',
        './data/processed'
    ]
    for path in ls_folders:
        if not os.path.exists(path):
            os.mkdir(path)


def insert_calculated_variables(raster, file, db_geo):
    """
    inser variables in db
    """
    for i in range(1, raster.number_bands+1):
        try:
            params_raster = raster.get_raster_params(i)
            time_raster = datetime.strptime(params_raster[0], 
                                            '%Y-%m-%d %H:%M:%S')
            # insert in db metadata
            row = [params_raster[1],
                   time_raster,
                   file,
                   'Processed']
            db_geo.inser_to_table(table=Download, ls_data=row)

        except Exception as e:
            print('ERROR: ', e)
            db_geo.update_table_download(table=Download,
                                         name=params_raster[1], 
                                         time=time_raster,
                                         value='Error')
    
    
def extract_raster_insert_db(raster, dbgeobio, layer_prov, file):
    # variable_atm = raster.get_raster_params(i)[1]
    print('Number of bands: ', raster.number_bands)
    ls_rows = raster.get_stats_provinces(layer_prov)
    dbgeobio.inser_many_to_table(table=StatsLayer, ls_data=ls_rows)  
    insert_calculated_variables(raster, file, dbgeobio)
    
    
def process_copernicus_data():
    create_folders()
    # get path of grib files 
    files = read_download_files()
    # db to register processed data
    db_geo = SqlDB(path=f"sqlite:///{settings.DB_PATH}",
                   meta=meta, Base=Base)
    for file in files:
        # read first and execute
        print(file)
        rpol = RasterExtraction(file)
        layer_prov = ProvPolygons(shape_path=settings.SHP_PROVINCIAS)
        rpol.create_doa_from_raster(layer=layer_prov)
        print(file, os.path.basename(file))
        # # Extract stats from raster and insert in db 
        # extract_raster_insert_db(raster=rpol.get_doa(), dbgeobio=db_geo, 
        #                          layer_prov=layer_prov, file=files[0])
        # extract_raster_insert_db(raster=rpol, dbgeobio=db_geo, 
        #                          layer_prov=layer_prov, file=files[0])
        # Copy and remove files from download folder
        shutil.copyfile(file, os.path.join(settings.PROCESSED_FOLDERS, os.path.basename(file)))
        os.remove(file) 
        layer_prov.clear()

        
def download_datagrib(client_cop, dc_download):
    prefix = dc_download['year']+dc_download['month']
    filename = f"{prefix}_{settings.COPERNICUS_CATALOGE}.grib"
    path_file = os.path.join(settings.DOWNLOAD_FOLDERS,filename)
    print(path_file)
    client_cop.retrieve(
        settings.COPERNICUS_CATALOGE,
        dc_download,
        path_file)

    
if __name__ == "__main__":    
    dc_vars = settings.DC_DOWNLOAD_COPERNICUS.copy()
    create_folders()
    # process_copernicus_data()
    c = cdsapi.Client()
    for year in range(2012, 2013):
        dc_vars['year'] = str(year)
        for month in range(8, 12):
            dc_vars['month'] = f"{month:02}"
            print(dc_vars['year'], dc_vars['month'])
            # download_datagrib(client_cop=c, dc_download=dc_vars)
            process_copernicus_data()
            # break
    
    print("--END--")
