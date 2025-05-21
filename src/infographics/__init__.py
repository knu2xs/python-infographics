__title__ = "infographics"
__version__ = "0.1.1"
__author__ = "Joel McCune (https://github.com/knu2xs)"
__license__ = "Apache 2.0"
__copyright__ = "Copyright 2025 by Joel McCune (https://github.com/knu2xs)"

__all__ = [
    "get_standard_infographics",
    "get_organization_infographics",
    "create_infographic",
]

from functools import cache
import json
from pathlib import Path
from typing import Optional, Union

from arcgis.env import active_gis
from arcgis.geometry import Geometry
from arcgis.geoenrichment import create_report
from arcgis.gis import GIS
import pandas as pd


def ensure_gis(gis: Optional[GIS]) -> GIS:
    """Helper function ensuring there is a GIS object to work with."""
    # try to get a GIS object from the current environment
    if gis is None:
        gis = active_gis

    # if gis still none, complain
    if gis is None:
        raise ValueError(
            "Please provide a valid GIS object or instantiate a GIS in the active workspace."
        )

    # make sure geoenrichment is configured for the GIS
    if (
        gis.properties.helperServices.geoenrichment is None
        or gis.properties.helperServices.geoenrichment.url is None
    ):
        raise ValueError(
            f"The provided GIS ({gis.__str__}) does not appear to have a Geoenrichment server configured."
        )

    return gis


@cache
def get_countries(gis: Optional[GIS]) -> pd.DataFrame:
    """
    Get a dataframe of the country ISO2 and hierarchy for constructing urls to introspectively retrieve default reports.

    Args:
        gis: GIS object instance with Geoenrichemnt services configured.
    """

    # construct the url for getting available countries
    countries_url = (
        gis.properties.helperServices.geoenrichment.url + "/Geoenrichment/Countries"
    )

    # use the gis object to make the request, handles authentication for us
    res = gis._con.get(countries_url)

    # convert the JSON to a pandas data frame, and only keep the columns we need
    countries_df = pd.json_normalize(res.get("countries")).loc[:, ["id", "hierarchies"]]

    # get just the id for each of the "hierarchies" we need for constructing urls
    countries_df["hierarchies"] = countries_df["hierarchies"].apply(
        lambda h_lst: [h_dict.get("ID") for h_dict in h_lst]
    )

    # get a row for each of the hierarchies with the country ISO2 code
    countries_df = countries_df.explode("hierarchies")

    return countries_df


def get_standard_infographics(
    country_iso2: str, gis: Optional[GIS] = None, hierarchy: Optional[str] = None
) -> pd.DataFrame:
    """
    Get a list of standard (default) infographics.

    Args:
        country_iso2: ISO code of the country to get infographics for.
        gis: GIS object instance with Geoenrichemnt services configured.
    """
    # ensure have a GIS to work with
    gis = ensure_gis(gis)

    # get the countries dataframe to use
    countries_df = get_countries(gis)

    # ensure iso2 provided is available
    if country_iso2 not in list(countries_df.id):
        raise ValueError(
            f'The ISO2 country code, "{country_iso2}" you provided does not appear to be available.'
        )

    # if a hierarchy is not provided, get the available hierarchies for the country
    if hierarchy is None:
        hrchy_lst = list(
            countries_df[countries_df["id"] == country_iso2]["hierarchies"]
        )

    # if just one hierarchy provided, put into a list for consistency
    elif isinstance(hierarchy, str):
        hrchy_lst = [hierarchy]

    # ensure the hierarchies are available for the given country
    invalid_lst = [
        h
        for h in hrchy_lst
        if h not in list(countries_df[countries_df["id"] == "US"]["hierarchies"])
    ]

    if len(invalid_lst):
        raise ValueError(
            f"The following heirarchies, {invalid_lst}, do not appear to be available for the specified country."
        )

    # create the dataframe for output
    out_df = pd.DataFrame(
        columns=[
            "reportID",
            "title",
            "itemID",
            "formats",
            "dataVintage",
            # 'dataVintageDescription',
            "countries",
            "hierarchy",
            "category",
        ]
    )

    # iterate the hierarchies
    for idx, hrchy in enumerate(hrchy_lst):
        # construct the url with the counry and hierarchy
        infographic_url = (
            gis.properties.helperServices.geoenrichment.url
            + f"/Geoenrichment/Infographics/Standard/{country_iso2}/{hrchy}"
        )

        # get the list of default infographics
        res = gis._con.get(infographic_url, params={"f": "json"})

        # pull out the reports
        reports = res.get("reports")

        # if there are any infographic reports to work with
        if reports is not None and len(reports) > 0:
            # create a data frame of default infographics
            std_ig_df = pd.json_normalize(reports)
            std_ig_df.columns = [
                col.replace("metadata.", "") for col in std_ig_df.columns
            ]
            std_ig_df = std_ig_df.loc[
                :,
                [
                    "reportID",
                    "title",
                    "itemID",
                    "formats",
                    "dataVintage",
                    # 'dataVintageDescription',
                    "countries",
                    "hierarchy",
                ],
            ]
            std_ig_df["category"] = "standard"

            # if the first pass, save to the output
            if idx == 0:
                out_df = std_ig_df.copy(deep=True)

            # otherwise, add the results onto what is already bee retrieved
            else:
                out_df = pd.concat([out_df, std_ig_df.copy(deep=True)])

    return out_df


def get_organization_infographics(gis: Optional[GIS]) -> pd.DataFrame:
    """
    Get available custom Infographics for an organization.

    Args:
        gis: GIS object instance with Geoenrichemnt services configured.
    """
    # ensure have GIS to work with
    gis = ensure_gis(gis)

    # get all report templates in the organization
    itm_lst = gis.content.search("type:Report Template")

    # get those with infographic in the type keywords
    ig_itm_lst = [
        itm
        for itm in itm_lst
        if any([kw for kw in itm.typeKeywords if "infographic" in kw.lower()])
    ]

    # reformat the response
    ig_dict_lst = []
    for itm in ig_itm_lst:
        itm_dict = {
            "title": itm.title,
            "itemID": itm.id,
            "itemDescription": itm.description,
            "countries": itm.properties.get("countries"),
            "formats": itm.properties.get("formats"),
            "owner": itm.owner,
        }
        ig_dict_lst.append(itm_dict)

    # convert the response into a dataframe
    cst_ig_df = pd.DataFrame(ig_dict_lst)

    # add custom category
    cst_ig_df["category"] = "custom"

    return cst_ig_df


def create_infographic(
    study_areas: Union[Geometry, list[Geometry]],
    infographic_id: str,
    out_path: Union[str, Path],
    export_format: Optional[str] = "pdf",
    gis: Optional[GIS] = None,
):
    """
    This is a pretty thin wrapper around the ``arcgis.geoenrichment.create_report``
    method to make it easier to run reports, specifically infographic reports, using
    the ArcGIS Python API.

    Args:
        study_areas: Either a single Geometry or list of Geometry objects.
        infographic_id: Web GIS Item id or ReportID for one of the standard Infographics.
        out_path: Path to where the output file will be saved.
        export_format: String for desired output format. Default is 'pdf'.
        gis: GIS object instance with Geoenrichemnt services configured.

    Returns:
        Path to where output report is stored.

    """
    # make sure gis has what we need
    gis = ensure_gis(gis)

    # ensure list of geometries if only one geometry inputted
    in_geom = [study_areas] if not isinstance(study_areas, list) else study_areas

    # ensure all inputs are valid geometries
    assert all([isinstance(geom, Geometry) for geom in in_geom])

    # format geometries as list of dicts so create_report leaves alone
    in_geom = [{"geometry": json.loads(geom.JSON)} for geom in in_geom]

    # validate export format
    export_format = export_format.lower()
    assert export_format in ["xlsx", "pdf", "html"]

    # get the directory and file name from path
    out_folder = str(out_path.parent)
    out_name = str(out_path.name)

    # ensure right extension is used
    file_extension = out_path.suffix.lstrip(".")
    if not (file_extension == "htm" and export_format == "html"):
        if export_format != file_extension:
            out_name = f"{out_name}.{export_format}"

    # get the report
    out_report = create_report(
        study_areas=in_geom,
        report=infographic_id,
        export_format=export_format,
        out_folder=out_folder,
        out_name=out_name,
        gis=gis,
    )

    return Path(out_report)
