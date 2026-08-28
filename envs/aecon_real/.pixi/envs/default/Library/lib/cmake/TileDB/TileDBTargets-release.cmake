#----------------------------------------------------------------
# Generated CMake target import file for configuration "Release".
#----------------------------------------------------------------

# Commands may need to know the format version.
set(CMAKE_IMPORT_FILE_VERSION 1)

# Import target "TileDB::tiledb_shared" for configuration "Release"
set_property(TARGET TileDB::tiledb_shared APPEND PROPERTY IMPORTED_CONFIGURATIONS RELEASE)
set_target_properties(TileDB::tiledb_shared PROPERTIES
  IMPORTED_IMPLIB_RELEASE "${_IMPORT_PREFIX}/lib/tiledb.lib"
  IMPORTED_LINK_DEPENDENT_LIBRARIES_RELEASE "aws-cpp-sdk-identity-management;aws-cpp-sdk-cognito-identity;aws-cpp-sdk-sts;aws-cpp-sdk-s3;aws-cpp-sdk-core;Azure::azure-identity;Azure::azure-storage-blobs;Azure::azure-storage-files-datalake;spdlog::spdlog;WebP::webp"
  IMPORTED_LOCATION_RELEASE "${_IMPORT_PREFIX}/bin/tiledb.dll"
  )

list(APPEND _cmake_import_check_targets TileDB::tiledb_shared )
list(APPEND _cmake_import_check_files_for_TileDB::tiledb_shared "${_IMPORT_PREFIX}/lib/tiledb.lib" "${_IMPORT_PREFIX}/bin/tiledb.dll" )

# Commands beyond this point should not need to know the version.
set(CMAKE_IMPORT_FILE_VERSION)
