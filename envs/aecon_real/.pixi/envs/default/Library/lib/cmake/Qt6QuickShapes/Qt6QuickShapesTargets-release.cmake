#----------------------------------------------------------------
# Generated CMake target import file for configuration "Release".
#----------------------------------------------------------------

# Commands may need to know the format version.
set(CMAKE_IMPORT_FILE_VERSION 1)

# Import target "Qt6::QuickShapes" for configuration "Release"
set_property(TARGET Qt6::QuickShapes APPEND PROPERTY IMPORTED_CONFIGURATIONS RELEASE)
set_target_properties(Qt6::QuickShapes PROPERTIES
  IMPORTED_IMPLIB_RELEASE "${_IMPORT_PREFIX}/lib/Qt6QuickShapes.lib"
  IMPORTED_LINK_DEPENDENT_LIBRARIES_RELEASE "Qt6::Gui;Qt6::Quick"
  IMPORTED_LOCATION_RELEASE "${_IMPORT_PREFIX}/lib/qt6/bin/Qt6QuickShapes.dll"
  )

list(APPEND _cmake_import_check_targets Qt6::QuickShapes )
list(APPEND _cmake_import_check_files_for_Qt6::QuickShapes "${_IMPORT_PREFIX}/lib/Qt6QuickShapes.lib" "${_IMPORT_PREFIX}/lib/qt6/bin/Qt6QuickShapes.dll" )

# Commands beyond this point should not need to know the version.
set(CMAKE_IMPORT_FILE_VERSION)
