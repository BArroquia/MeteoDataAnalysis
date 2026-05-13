"""
Author: Benjamin Arroquia Cuadros
Download data from copernicus CDS
"""

import cdsapi
import os
import pandas as pd
import shutil
import zipfile
import netCDF4
import sqlite3
import time

# dataset = "cams-global-reanalysis-eac4"
# request = {
#     "variable": [
#         "particulate_matter_1um",
#         # "particulate_matter_2.5um",
#         # "particulate_matter_10um",
#         # "total_column_ozone"
#     ],
#     "date": ["2016-12-01/2017-01-02"],
#     # "date": ["2003-08-31/2018-08-31"],
#     "time": [
#         "00:00", "06:00", "12:00",
#         "18:00"
#     ],
#     "data_format": "grib"
# }
# target = 'data'


# https://ads.atmosphere.copernicus.eu/datasets/cams-europe-air-quality-reanalyses?tab=download
# dataset = "cams-europe-air-quality-reanalyses"
# request = {
#     "variable": [
#         "ozone",
#         "particulate_matter_2.5um"
#     ],
#     "model": ["ensemble"],
#     "level": ["0"],
#     "type": ["validated_reanalysis"],
#     "year": ["2013"],
#     "month": ["01"]
# }

# https://ads.atmosphere.copernicus.eu/datasets/cams-global-reanalysis-eac4-monthly?tab=download
# 200301_pm25_10_oz_ae396b3935f4e7758b8eb1e96515544
# dataset = "cams-global-reanalysis-eac4-monthly"
# request = {
#     "variable": [
#         "particulate_matter_2.5um",
#         "particulate_matter_10um",
#         "ozone"
#     ],
#     "year": ["2003"],
#     "month": ["01"],
#     "product_type": ["monthly_mean_by_hour_of_day"],
#     "time": ["00:00", "06:00", "12:00", "18:00"],
#     "data_format": "netcdf_zip"
# }

# https://ads.atmosphere.copernicus.eu/datasets/cams-global-reanalysis-eac4?tab=download
# 200309_cam_pm25_o3_eb9fdef4b0ea5d4c4fffdf1b385a39f6.grib

def download_cds(date):
    """
    date="2003-09-30/2003-10-10"
    """
    dataset = "cams-global-reanalysis-eac4"
    request = {
        "variable": [
            "particulate_matter_1um",
            "particulate_matter_2.5um",
            "particulate_matter_10um",
            "total_column_nitrogen_dioxide",
            "total_column_ozone"
        ],
        "date": [date],
        "time": [
            "00:00", "06:00", "12:00",
            "18:00"
        ],
        "data_format": "netcdf_zip"
    }

    # Descarga
    print(os.getcwd(), os.listdir('./data'))
    client = cdsapi.Client()
    client.retrieve(dataset, request).download()


def get_date_to_download(start_date):
    end_date = '2019-01-01'
    date_range = pd.date_range(start=start_date, end=end_date, freq='D')
    # 2. Create a DataFrame
    df = pd.DataFrame({'datetime': date_range})
    df = df.set_index('datetime')
    monthly_groups = df.groupby(pd.Grouper(freq='M'))
    ls_download = []
    for month, group in monthly_groups:
        # print(f"\n--- Statistics for {month.strftime('%Y-%m')}-01 ---")
        ls_download.append(f"{month.strftime('%Y-%m')}-01")
    resultado = f"{ls_download[0]}/{ls_download[1]}"
    if start_date == end_date:
        resultado = None
    return resultado


def move_file_down(folder_dest_name):
    # Define the path to the zip file
    print(os.getcwd(), os.listdir('./'))
    lsf = [f for f in os.listdir('./') if '.zip' in f]
    source_zip_path = lsf[0]

    # Define the destination folder to move the zip file to
    destination_move_folder = './data/copernicus'

    # Define the folder where you want to extract the contents
    extraction_folder = f"./data/copernicus/{folder_dest_name}"

    try:
        # 1. Move the zip file
        os.makedirs(destination_move_folder, exist_ok=True)  # Create the destination folder if it doesn't exist
        destination_zip_path = os.path.join(destination_move_folder, os.path.basename(source_zip_path))
        shutil.move(source_zip_path, destination_zip_path)
        print(f"Successfully moved '{source_zip_path}' to '{destination_zip_path}'")

        # 2. Extract the zip file
        os.makedirs(extraction_folder, exist_ok=True)  # Create the extraction folder if it doesn't exist
        with zipfile.ZipFile(destination_zip_path, 'r') as zip_ref:
            zip_ref.extractall(extraction_folder)
        print(f"Successfully extracted '{destination_zip_path}' to '{extraction_folder}'")

    except FileNotFoundError:
        print(f"Error: Source zip file '{source_zip_path}' not found.")
        extraction_folder = None
    except Exception as e:
        print(f"An error occurred: {e}")
        extraction_folder = None
    return extraction_folder

def get_data_netcdf(netcffile):
    netcdf_file_path = os.path.join(netcffile, 'data_sfc.nc')
    with netCDF4.Dataset(netcdf_file_path, 'r') as nc_file:
        ls_var = netCDF4.num2date(nc_file['valid_time'][:], 
                                  nc_file['valid_time'].units)
        pandas_datetime_list_iter = []
        for cf_time in ls_var.data:
            pandas_datetime_list_iter.append(pd.Timestamp(cf_time.year, cf_time.month, cf_time.day,
                                                        cf_time.hour, cf_time.minute, cf_time.second))
        df = pd.DataFrame(pandas_datetime_list_iter, columns=['date'])
        df['date'] = pd.to_datetime(df['date'])
        df['path'] = netcffile
    # To a ddbb
    database_name = 'timeseries_download.db'
    conn = sqlite3.connect(database_name)
    df.to_sql(name="netcdfdownloaded", con=conn, if_exists='replace')
    conn.close()
    print(df.tail())

def get_last_month():
    database_name = 'timeseries_download.db'
    conn = sqlite3.connect(database_name)
    df = pd.read_sql(sql="select * from netcdfdownloaded", con=conn)
    conn.close()
    df['date'] = pd.to_datetime(df['date'])
    df.sort_values("date", inplace=True)
    print(df.iloc[-1, :]["date"].strftime('%Y-%m-%d'))
    return df.iloc[-1, :]["date"].strftime('%Y-%m-%d')


if __name__ == '__main__':
    next_date = get_last_month()
    while next_date is not None:
        next_date = get_last_month()
        date_down = get_date_to_download(start_date=next_date)
        if date_down is not None:
            print(date_down)
            download_cds(date=date_down)
            print(os.getcwd(), os.listdir('./'))
            date_down = date_down.replace("/", "-")
            print(date_down)
            folder_data = move_file_down(folder_dest_name=date_down)
            get_data_netcdf(netcffile=folder_data)
        else:
            next_date = date_down
        print("--- DOWNLOADED sleep")
        time.sleep(10)
    #TODO:COnvert raster origin and pout las downloaded into bbdd