#----------------------------------------------------------------
# Generated CMake target import file for configuration "Release".
#----------------------------------------------------------------

# Commands may need to know the format version.
set(CMAKE_IMPORT_FILE_VERSION 1)

# Import target "Qt6::LabsStyleKitImpl" for configuration "Release"
set_property(TARGET Qt6::LabsStyleKitImpl APPEND PROPERTY IMPORTED_CONFIGURATIONS RELEASE)
set_target_properties(Qt6::LabsStyleKitImpl PROPERTIES
  IMPORTED_IMPLIB_RELEASE "${_IMPORT_PREFIX}/lib/Qt6LabsStyleKitImpl.lib"
  IMPORTED_LINK_DEPENDENT_LIBRARIES_RELEASE "Qt6::Gui;Qt6::Quick;Qt6::QuickTemplates2;Qt6::LabsStyleKit;Qt6::Qml;Qt6::Core"
  IMPORTED_LOCATION_RELEASE "${_IMPORT_PREFIX}/lib/qt6/bin/Qt6LabsStyleKitImpl.dll"
  )

list(APPEND _cmake_import_check_targets Qt6::LabsStyleKitImpl )
list(APPEND _cmake_import_check_files_for_Qt6::LabsStyleKitImpl "${_IMPORT_PREFIX}/lib/Qt6LabsStyleKitImpl.lib" "${_IMPORT_PREFIX}/lib/qt6/bin/Qt6LabsStyleKitImpl.dll" )

# Commands beyond this point should not need to know the version.
set(CMAKE_IMPORT_FILE_VERSION)
