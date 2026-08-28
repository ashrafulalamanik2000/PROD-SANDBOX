/*
 * pdal_features.hpp.in is used by cmake to generate pdal_features.hpp
 *
 * Do not edit pdal_features.hpp
 *
 */
#pragma once

#include <string>

/*
 * version settings
 */
#define PDAL_VERSION_MAJOR 2
#define PDAL_VERSION_MINOR 10
#define PDAL_VERSION_PATCH 2

namespace pdal
{

const int pdalVersionMajor { 2 };
const int pdalVersionMinor { 10 };
const int pdalVersionPatch { 2 };
const std::string pdalVersion { "2.10.2" };
const std::string pdalSha { "Release" };

} // namespace pdal

#define PDAL_PLUGIN_INSTALL_PATH "bin"
/*
 * availability of 3rd-party libraries
 */
/* #undef PDAL_HAVE_HDF5 */
/* #undef PDAL_HAVE_ZSTD */
#define PDAL_HAVE_ZLIB
/* #undef PDAL_HAVE_LZMA */
#define PDAL_HAVE_LIBXML2
#define PDAL_LAS_START

/*
 * Debug or Release build?
 */
#define PDAL_BUILD_TYPE "Release"

/*
 * built pdal app as application bundle on OSX?
 */
/* #undef PDAL_APP_BUNDLE */


