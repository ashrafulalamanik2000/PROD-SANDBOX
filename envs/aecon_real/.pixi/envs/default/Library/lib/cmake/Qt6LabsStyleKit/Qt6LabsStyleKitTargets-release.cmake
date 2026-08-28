#----------------------------------------------------------------
# Generated CMake target import file for configuration "Release".
#----------------------------------------------------------------

# Commands may need to know the format version.
set(CMAKE_IMPORT_FILE_VERSION 1)

# Import target "Qt6::LabsStyleKit" for configuration "Release"
set_property(TARGET Qt6::LabsStyleKit APPEND PROPERTY IMPORTED_CONFIGURATIONS RELEASE)
set_target_properties(Qt6::LabsStyleKit PROPERTIES
  IMPORTED_IMPLIB_RELEASE "${_IMPORT_PREFIX}/lib/Qt6LabsStyleKit.lib"
  IMPORTED_LINK_DEPENDENT_LIBRARIES_RELEASE "Qt6::Gui;Qt6::Quick;Qt6::QuickTemplates2;Qt6::QuickControls2Impl;Qt6::Qml;Qt6::Core"
  IMPORTED_LOCATION_RELEASE "${_IMPORT_PREFIX}/lib/qt6/bin/Qt6LabsStyleKit.dll"
  )

list(APPEND _cmake_import_check_targets Qt6::LabsStyleKit )
list(APPEND _cmake_import_check_files_for_Qt6::LabsStyleKit "${_IMPORT_PREFIX}/lib/Qt6LabsStyleKit.lib" "${_IMPORT_PREFIX}/lib/qt6/bin/Qt6LabsStyleKit.dll" )

# Commands beyond this point should not need to know the version.
set(CMAKE_IMPORT_FILE_VERSION)
