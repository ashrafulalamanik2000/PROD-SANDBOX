#----------------------------------------------------------------
# Generated CMake target import file for configuration "Release".
#----------------------------------------------------------------

# Commands may need to know the format version.
set(CMAKE_IMPORT_FILE_VERSION 1)

# Import target "pdalcpp" for configuration "Release"
set_property(TARGET pdalcpp APPEND PROPERTY IMPORTED_CONFIGURATIONS RELEASE)
set_target_properties(pdalcpp PROPERTIES
  IMPORTED_IMPLIB_RELEASE "${_IMPORT_PREFIX}/lib/pdalcpp.lib"
  IMPORTED_LINK_DEPENDENT_LIBRARIES_RELEASE "GDAL::GDAL;PROJ::proj"
  IMPORTED_LOCATION_RELEASE "${_IMPORT_PREFIX}/bin/pdalcpp.dll"
  )

list(APPEND _cmake_import_check_targets pdalcpp )
list(APPEND _cmake_import_check_files_for_pdalcpp "${_IMPORT_PREFIX}/lib/pdalcpp.lib" "${_IMPORT_PREFIX}/bin/pdalcpp.dll" )

# Commands beyond this point should not need to know the version.
set(CMAKE_IMPORT_FILE_VERSION)
