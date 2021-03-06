import sys
import os.path
from PackageUtils import PackageUtils
from Logger import Logger
from ToolChainUtils import ToolChainUtils
from CommandUtils import CommandUtils
from constants import constants
from SpecData import SPECS
from StringUtils import StringUtils
from Sandbox import Chroot, Container

class PackageBuilderBase(object):
    def __init__(self, mapPackageToCycles, pkgBuildType):
        # will be initialized in buildPackageFunction()
        self.logName = None
        self.logPath = None
        self.logger = None
        self.package = None
        self.version = None
        self.mapPackageToCycles = mapPackageToCycles
        self.listNodepsPackages = ["glibc", "gmp", "zlib", "file", "binutils", "mpfr",
                                   "mpc", "gcc", "ncurses", "util-linux", "groff", "perl",
                                   "texinfo", "rpm", "openssl", "go"]
        self.pkgBuildType = pkgBuildType

    def buildPackageFunction(self, pkg):
        packageName, packageVersion = StringUtils.splitPackageNameAndVersion(pkg)
        #do not build if RPM is already built
        #test only if the package is in the testForceRPMS with rpmCheck
        #build only if the package is not in the testForceRPMS with rpmCheck
        if self._checkIfPackageIsAlreadyBuilt(packageName, packageVersion):
            if not constants.rpmCheck:
                return
            elif constants.rpmCheck and self.package not in constants.testForceRPMS:
                return

        self._buildPackagePrepareFunction(packageName, packageVersion)
        try:
            self._buildPackage()
        except Exception as e:
            # TODO: self.logger might be None
            self.logger.exception(e)
            raise e

    def _buildPackagePrepareFunction(self, package, version):
        self.package = package
        self.version = version
        self.logName = "build-" + package + "-" + version
        self.logPath = constants.logPath + "/" + package + "-" + version
        if not os.path.isdir(self.logPath):
            cmdUtils = CommandUtils()
            cmdUtils.runCommandInShell("mkdir -p " + self.logPath)
        self.logger = Logger.getLogger(self.logName, self.logPath, constants.logLevel)

    def _findPackageNameAndVersionFromRPMFile(self, rpmfile):
        rpmfile = os.path.basename(rpmfile)
        releaseindex = rpmfile.rfind("-")
        if releaseindex == -1:
            self.logger.error("Invalid rpm file:" + rpmfile)
            return None
        pkg = rpmfile[0:releaseindex]
        return pkg

    def _findInstalledPackages(self, sandbox):
        pkgUtils = PackageUtils(self.logName, self.logPath)
        listInstalledRPMs = pkgUtils.findInstalledRPMPackages(sandbox)
        listInstalledPackages = []
        for installedRPM in listInstalledRPMs:
            pkg = self._findPackageNameAndVersionFromRPMFile(installedRPM)
            if pkg is not None:
                listInstalledPackages.append(pkg)
        return listInstalledPackages, listInstalledRPMs

    def _checkIfPackageIsAlreadyBuilt(self, package, version):
        basePkg = SPECS.getData().getSpecName(package)
        listRPMPackages = SPECS.getData().getRPMPackages(basePkg, version)
        packageIsAlreadyBuilt = True
        pkgUtils = PackageUtils()
        for pkg in listRPMPackages:
            if pkgUtils.findRPMFileForGivenPackage(pkg, version) is None:
                packageIsAlreadyBuilt = False
                break
        return packageIsAlreadyBuilt

    def _findRunTimeRequiredRPMPackages(self, rpmPackage, version):
        return SPECS.getData().getRequiresForPackage(rpmPackage, version)

    def _findBuildTimeRequiredPackages(self):
        return SPECS.getData().getBuildRequiresForPackage(self.package, self.version)

    def _findBuildTimeCheckRequiredPackages(self):
        return SPECS.getData().getCheckBuildRequiresForPackage(self.package, self.version)

    def _installPackage(self, pkgUtils, package, packageVersion, sandbox, destLogPath,
                        listInstalledPackages, listInstalledRPMs):
        rpmfile = pkgUtils.findRPMFileForGivenPackage(package,packageVersion);
        if rpmfile is None:
            self.logger.error("No rpm file found for package: " + package + "-" + packageVersion)
            raise Exception("Missing rpm file")
        specificRPM = os.path.basename(rpmfile.replace(".rpm", ""))
        pkg = package+"-"+packageVersion
        if pkg in listInstalledPackages:
                return
        # mark it as installed -  to avoid cyclic recursion
        listInstalledPackages.append(pkg)
        listInstalledRPMs.append(specificRPM)
        self._installDependentRunTimePackages(pkgUtils, package, packageVersion, sandbox, destLogPath,
                                              listInstalledPackages, listInstalledRPMs)
        noDeps = False
        if (package in self.mapPackageToCycles or
                package in self.listNodepsPackages or
                package in constants.noDepsPackageList):
            noDeps = True
        pkgUtils.prepRPMforInstall(package,packageVersion, noDeps, destLogPath)

    def _installDependentRunTimePackages(self, pkgUtils, package, packageVersion, sandbox, destLogPath,
                                         listInstalledPackages, listInstalledRPMs):
        listRunTimeDependentPackages = self._findRunTimeRequiredRPMPackages(package, packageVersion)
        if listRunTimeDependentPackages:
            for pkg in listRunTimeDependentPackages:
                if pkg in self.mapPackageToCycles:
                    continue
                packageName, packageVersion = StringUtils.splitPackageNameAndVersion(pkg)
                latestPkgRPM = os.path.basename(
                    pkgUtils.findRPMFileForGivenPackage(packageName, packageVersion)).replace(".rpm", "")
                if pkg in listInstalledPackages and latestPkgRPM in listInstalledRPMs:
                    continue
                self._installPackage(pkgUtils, packageName,packageVersion, sandbox, destLogPath,listInstalledPackages, listInstalledRPMs)

    def _findDependentPackagesAndInstalledRPM(self, sandbox):
        listInstalledPackages, listInstalledRPMs = self._findInstalledPackages(sandbox)
        self.logger.debug(listInstalledPackages)
        listDependentPackages = self._findBuildTimeRequiredPackages()
        listTestPackages=[]
        if constants.rpmCheck and self.package in constants.testForceRPMS:
            # One time optimization
            if constants.listMakeCheckRPMPkgWithVersionstoInstall is None:
                constants.listMakeCheckRPMPkgWithVersionstoInstalli=[]
                for package in constants.listMakeCheckRPMPkgtoInstall:
                    version = SPECS.getData().getHighestVersion(package)
                    constants.listMakeCheckRPMPkgWithVersionstoInstall.append(package+"-"+version)

            listDependentPackages.extend(self._findBuildTimeCheckRequiredPackages())
            testPackages = (set(constants.listMakeCheckRPMPkgWithVersionstoInstall) -
                            set(listInstalledPackages) -
                            set([self.package+"-"+self.version]))
            listTestPackages=list(set(testPackages))
            listDependentPackages = list(set(listDependentPackages))
        return listDependentPackages, listTestPackages, listInstalledPackages, listInstalledRPMs

class PackageBuilderContainer(PackageBuilderBase):
    def __init__(self, mapPackageToCycles, pkgBuildType):
        PackageBuilderBase.__init__(self, mapPackageToCycles, pkgBuildType)

    def _buildPackage(self):
        #should initialize a logger based on package name
        containerTaskName = "build-" + self.package + "-" + self.version
        container = None
        try:
            container = Container(self.logger)
            container.create(containerTaskName)

            tcUtils = ToolChainUtils(self.logName, self.logPath)
            tcUtils.installCustomToolChainRPMS(container, self.package, self.version)

            listDependentPackages, listTestPackages, listInstalledPackages, listInstalledRPMs = (
                self._findDependentPackagesAndInstalledRPM(container))

            pkgUtils = PackageUtils(self.logName, self.logPath)

            if listDependentPackages:
                self.logger.debug("Installing the build time dependent packages......")
                for pkg in listDependentPackages:
                    packageName, packageVersion = StringUtils.splitPackageNameAndVersion(pkg)
                    self._installPackage(pkgUtils, packageName, packageVersion, container, self.logPath,listInstalledPackages, listInstalledRPMs)
                for pkg in listTestPackages:
                    flag = False
                    packageName, packageVersion = StringUtils.splitPackageNameAndVersion(pkg)
                    for depPkg in listDependentPackages:
                        depPackageName, depPackageVersion = StringUtils.splitPackageNameAndVersion(depPkg)
                        if depPackageName == packageName:
                            flag = True
                            break;
                    if flag == False:
                        self._installPackage(pkgUtils, packageName,packageVersion, container, self.logPath,listInstalledPackages, listInstalledRPMs)
                pkgUtils.installRPMSInOneShot(container)
                self.logger.debug("Finished installing the build time dependent packages....")

            self.logger.debug("BuildContainer-buildPackage: Start building the package: " +
                             self.package)
            pkgUtils.adjustGCCSpecs(container, self.package, self.version)
            pkgUtils.buildRPMSForGivenPackage(container, self.package, self.version, self.logPath)
            self.logger.debug("BuildContainer-buildPackage: Successfully built the package: " +
                             self.package)
        except Exception as e:
            self.logger.error("Failed while building package:" + self.package)
            if container is not None:
                self.logger.debug("Container " + container.getID() +
                                  " retained for debugging.")
            logFileName = os.path.join(self.logPath, self.package + ".log")
            fileLog = os.popen('tail -n 20 ' + logFileName).read()
            self.logger.debug(fileLog)
            raise e

        # Remove the container
        if container:
            container.destroy()

class PackageBuilderChroot(PackageBuilderBase):
    def __init__(self, mapPackageToCycles, pkgBuildType):
        PackageBuilderBase.__init__(self, mapPackageToCycles, pkgBuildType)

    def _buildPackage(self):
        chroot = None
        try:
            chroot = Chroot(self.logger)
            chroot.create(self.package + "-" + self.version)

            tUtils = ToolChainUtils(self.logName, self.logPath)
            tUtils.installToolChainRPMS(chroot, self.package, self.version, self.logPath)

            listDependentPackages, listTestPackages, listInstalledPackages, listInstalledRPMs = (
                self._findDependentPackagesAndInstalledRPM(chroot))

            pkgUtils = PackageUtils(self.logName, self.logPath)

            if listDependentPackages:
                self.logger.debug("Installing the build time dependent packages......")
                for pkg in listDependentPackages:
                    packageName, packageVersion = StringUtils.splitPackageNameAndVersion(pkg)
                    self._installPackage(pkgUtils, packageName, packageVersion, chroot, self.logPath,listInstalledPackages, listInstalledRPMs)
                for pkg in listTestPackages:
                    flag = False
                    packageName, packageVersion = StringUtils.splitPackageNameAndVersion(pkg)
                    for depPkg in listDependentPackages:
                        depPackageName, depPackageVersion = StringUtils.splitPackageNameAndVersion(depPkg)
                        if depPackageName == packageName:
                            flag = True
                            break;
                    if flag == False:
                        self._installPackage(pkgUtils, packageName,packageVersion, chroot, self.logPath,listInstalledPackages, listInstalledRPMs)
                pkgUtils.installRPMSInOneShot(chroot)
                self.logger.debug("Finished installing the build time dependent packages....")

            pkgUtils.adjustGCCSpecs(chroot, self.package, self.version)
            pkgUtils.buildRPMSForGivenPackage(chroot, self.package, self.version,
                                              self.logPath)
            self.logger.debug("Successfully built the package:" + self.package)
        except Exception as e:
            self.logger.error("Failed while building package:" + self.package)
            self.logger.debug("Chroot: " + chroot.getPath() +
                              " not deleted for debugging.")
            logFileName = os.path.join(self.logPath, self.package + ".log")
            fileLog = os.popen('tail -n 100 ' + logFileName).read()
            self.logger.info(fileLog)
            raise e
        if chroot:
            chroot.destroy()
