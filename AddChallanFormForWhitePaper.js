var record = -1; //For Denomination Grid // dataindex
var totalAmount = 0;
var challanModel;
var queryStringName = "";
var queryStringvCount = -1;
var copyingFeeAmount;

let otpTotalSeconds = 0;
let otpTimer = null;
let otpTotalSecondsOnSave = 0;
let otpTimerOnSave = null;
function PrintChallan() {
    //window.location = '../GeneratePDF/Challan/?id=' + printSerial + '&AdhesiveStamp=_For_AdhesiveStamps';
    window.location = '../GeneratePDF/Challan/' + printSerial;
}

function englishToUrdu_AddChallan() {
    $('#districtFloatingLbl').html('ضلع');
    $('#tehsilFloatingLbl').html('تحصیل');
    $('#stampPaperTypeFloatingLbl').html('اسٹامپ پیپرکی قسم');
    $('#deedNameFloatingLbl').html('Deed Name');
    $('#saleDeedNoteLbl').html(ForSaleDeedpleaseuseConveyance);
    $('#districtFloatingLblReadOnly').html('District');
    $('#tehsilFloatingLblReadOnly').html('Tehsil');
    $('#stampPaperTypeFloatingLblReadOnly').html('Stamp Paper Type');
    $('#deedNameFloatingLblReadOnly').html('Deed Name');
    $('#oldRegNumFloatingLbl').html('Old Registry Number');
    $('#oldRegDateFloatingLbl').html('Old Registry Date');
    $('#agentNameFloatingLbl').html('Agent Name');
    $('#agentCnicFloatingLbl').html('Agent CNIC');
    $('#agentContactFloatingLbl').html('Agent Contact');
    $('#agentEmailFloatingLbl').html('Agent Email');
}

function urduToEnglish_AddChallan() {
    $('#districtFloatingLbl').html('District');
    $('#tehsilFloatingLbl').html('Tehsil');
    $('#stampPaperTypeFloatingLbl').html('Stamp Paper Type');
    $('#deedNameFloatingLbl').html('Deed Name');
    $('#saleDeedNoteLbl').html(ForSaleDeedpleaseuseConveyance);
    $('#districtFloatingLblReadOnly').html('District');
    $('#tehsilFloatingLblReadOnly').html('Tehsil');
    $('#stampPaperTypeFloatingLblReadOnly').html('Stamp Paper Type');
    $('#deedNameFloatingLblReadOnly').html('Deed Name');
    $('#oldRegNumFloatingLbl').html('Old Registry Number');
    $('#oldRegDateFloatingLbl').html('Old Registry Date');
    $('#agentNameFloatingLbl').html('Agent Name');
    $('#agentCnicFloatingLbl').html('Agent CNIC');
    $('#agentContactFloatingLbl').html('Agent Contact');
    $('#agentEmailFloatingLbl').html('Agent Email');
}

function toDate(stringDate) {
    return new Date(stringDate);
}

function ReasonValidation() {
    //debugger;
    var reasonText = $('#Reason').val();
    if (reasonText.trim() == "") {
        $('#Reason').val('');
        //$('#Reason').clea();
    }
    //alert(reasonText);
}
function printDiv() {
    debugger;
    //var divToPrint = document.getElementById('printarea');

    //var newWin = window.open('', 'Print-Window');

    //newWin.document.open();

    //newWin.document.write('<html><body onload="window.print()">' + divToPrint.innerHTML + '</body></html>');

    //newWin.document.close();

    //setTimeout(function () { newWin.close(); }, 10);
      //Hide all other elements other than printarea.
    $("#printarea").show();
    window.print();
}
function formatDate(stringDate) {
    var d = toDate(stringDate);
    var day = d.getDate();
    var month = d.getMonth() + 1;
    var year = d.getFullYear();
    var date = day + "/" + month + "/" + year;
    return date;
}

function getChallanModel() {
    debugger;
    var agentEmail;
    //var OtpContactForWhitePaperAgent = "";
    if ($('#agentEmail').val() == null || $('#agentEmail').val() == "") {
        agentEmail = "";
    }
    else {
        agentEmail = $('#agentEmail').val();
    }
    var agentName = $('#AgentName').val();
    var agentCnic = $('#AgentCnic').val(); 
    var agentCell = $('#AgentCell').val();
    var email = "";
    

    if (agentName === "" || agentName === null) {
        agentName = $("#PersonName").val();
    }
    if (agentCnic === "" || agentCnic === null) {
        agentCnic = $("#PersonCnic").val();
    }
    if (agentCell === "" || agentCell === null) {
        agentCell = $("#PersonPhone").val();
    }
   
    //if ($("#PersonPhone").val() === "" || $("#PersonPhone").val() === null) {
    //    $("#PersonPhone").val() = OtpContact;
    //}

    if ($('#PersonEmail').val() == null || $('#PersonEmail').val() == "") {
        email = "";
    }
    else {
        email = $('#PersonEmail').val();
    }
    if (agentEmail === "" || agentEmail === null) {
        agentEmail = email;
    }
    var personPhoneValue = $("#PersonPhone").val();

    var ApplicantData = [{
        PersonName: $("#PersonName").val(),
        IsPrimary: true,
        IsThroughPowerOfAttorney: false,
        PersonCnic: $("#PersonCnic").val(),
        PersonEmail: email,
        PersonAddress: $("#PersonAddress").val(),
        PersonPhoneMasked: maskContact(personPhoneValue),
        PersonPhone: $("#PersonPhone").val(),
        //OtpContactForPerson: OtpContactPerson, //Comment code..
        RelationName: $("#RelationName").val(),
        RelationId: $("#Relation").val(),
        RelationString: $("#Relation").data("kendoDropDownList").text(),
        NameString: $("#PersonName").val() + " " + $("#Relation").data("kendoDropDownList").text() + " " + $("#RelationName").val(),
        IsPrimaryString: true
    }]
    var deedInfoModel = {
        IsCalculateSum: false
    };

    //var registrationDate = $("#RegistrationDatePicker").data("kendoDatePicker").value();
    //var RegistrationDate = (registrationDate.getMonth() + 1) + '/' + (registrationDate.getDate()) + '/' + (registrationDate.getFullYear() + " 00:00:00");

    var ReasonOfWhitePaper = $("#Reason").val();
    ReasonOfWhitePaper = ReasonOfWhitePaper.replace(/\s+/g, " ");

    var challan1 = {
        AgentName: agentName,
        AgentCnic: agentCnic,
        AgentEmail: agentEmail,
        AgentCell: agentCell,
        //OtpContactForAgent: OtpContactAgent, //code comment..
        RegistrationFeeString: "",//$('#Payable_Reg_Duty').val(),
        PayableCvtString: "",//$('#Payable_CVT').val(),
        PayableStampDutyString: "",//$('#Stamp_duty').val(),
        SuitFor: "",//$('#SuitFor').val(),
        TransactionType: "",
        TransactionTypeString: transactionType,
        TransactionName: "",
        TransactionNameString: "",
        numberOfStampPapers: 1,
        DistrictId: $('#District').val(),
        DistrictString: $('#District').data("kendoDropDownList").text(),
        //BranchId: $('#Branch').val(),
        //BranchString: $('#Branch').data("kendoDropDownList").text(),
        TehsilId: $('#Tehsil').val(),
        TehsilString: $('#Tehsil').data("kendoDropDownList").text(),
        
        //DeedNumber: $("#DeedNumber").val(),
        //BookNumber: $("#BookNumber").val(),
        //VolumeNumber: $("#VolumeNumber").val(),
        //RegistryDate: "",
        //ReasonForNaqal: "",
        PurposeOfWhitePaper: $("#Purpose").val(),
        PurposeString: $('#Purpose').data("kendoDropDownList").text(),
        DenominationIDOfWhitePaper: DenominationId,
        DenominationOfWhitePaper:$("#Denomination").val(),
        ReasonOfWhitePaper:ReasonOfWhitePaper,


        applyRegistrationDuty: false,
        applyCVT: false,
        applyStampDuty: false, //new //////////////////////////////////////////////////////////
        DCValuationType: true,
        ActualDCValue: false,

        //numberOfStampPapers: null,                              //new //////////////////////////////////////////////////////////
        leasePeriod: "",                                     //new //////////////////////////////////////////////////////////
        TotalLeaseMoney: null,
        deficientAmount: "",                                 //new //////////////////////////////////////////////////////////
        penalty: "",                                         //new //////////////////////////////////////////////////////////
        totalDeficient: "",                                 //new //////////////////////////////////////////////////////////
        ChallanAmountPaidByString: "Applicant",
        ChallanAmountPaidBy:90,
        //StampDutyPaidBy: purchaserSellerValue,
        Party2: null,
        Party1: ApplicantData,
        TotalAmount: "",// $("#Total_Amount").val().replace(/,/g, ""),
        stampModel: null,
        propertyInfo: null,
        propertyInfo2: null,
        AdvanceMoney: "",//new //////////////////////////////////////////////////////////
        Premium: "",//new //////////////////////////////////////////////////////////
        TotalAmountOfDuties: 0,
        ChallanType: "Low_Denomination_Stamps",
        isPurchaser: false,
        AmountLabelText: "",
        isPropertyInfoApplicable: false,
        isCVTandNotDC: false,
        isPowerOfAttorney: false,
        visitorNumber: null,
        lstTaxAmountValue: null,
        DeedInfo: deedInfoModel,
        isMultiplePropertiesExchageOfProperty: false,
        isExchangeOfProperty: false,
        RegistryFeeString: "",
        RegistryFee: null,
        isRegistryFeeCheck: false,
        isDCFirstScreen: false,
        oldRegistryDate: "",
        oldRegistryNumber: null,
        isLeaseYearLessThan20: false
    }
    challan1.RegistrationFeeString = null;
    challan1.PayableCvtString = null;
    challan1.PayableStampDutyString = null;
    challan1.isOldRegistryChallan = false;
    return challan1;
}

function onToolTipMouseOver(_this)
{
    var id = _this.id;
    var title = _this.title;

    console.log('Tool Tip icon mouse over: ' + id + " , " + title);
    
    // Show the tool tip
    var tooltip = $("#" + id + "").kendoTooltip({
        content: function (e) {
            var target = e.target; // the element for which the tooltip is shown
            var tooltipContent = "";
            switch (id) {
                case "tooltip_LandPropertyValue2": tooltipContent = "Land Value of the second property"; break;
                case "tooltip_ConstructedStructureValueSecond": tooltipContent = "Constructed Structure Value of the second property"; break;

                case "tooltip_LandPropertyValue": tooltipContent = "Land Value of the property"; break;
                case "tooltip_ConstructedStructureValue": tooltipContent = "Constructed Structure Value of the property"; break;

                case "tooltip_LeasePeriodGenerateChallan": tooltipContent = "Lease Period in years"; break;
                case "tooltip_TotalLeaseMoneyGenerateChallan": tooltipContent = "Total Lease Money is used to calculate CVT on the property"; break;
                case "tooltip_AverageAnnualRent": tooltipContent = "Total Annual Rent"; break;
                default: tooltipContent = title; break;
            }

            return "Required. " + tooltipContent;
        },
        position: "top"
    }).data("kendoTooltip");

    tooltip.show($("#" + id + "")); 
}


function displayCaptchaError(message) {
    const captchaErrorElement = document.getElementById("captchaError");

    if (captchaErrorElement) {
        captchaErrorElement.innerHTML = message; // Set the error message
        captchaErrorElement.style.color = "red"; // Set text color to red
        captchaErrorElement.style.display = "block"; // Make the error message visible
    }
}

function maskContact(contactNo) {
    if (!contactNo) return "";
    return "*********" + contactNo.slice(-3);
}

function CaptchaCheck(res) {
  
    let $userCaptcha, $captchaCodeText, $capImage, $capImageText;
    const captchaErrorElement = document.getElementById("captchaError");

    // Select CAPTCHA-related elements
    $captchaCodeText = $("#CaptchaCode");
    $capImage = $("#CaptchaImage");
    $capImageText = $("#CapImageText");

    console.log("CaptchaId:", $capImageText.val()); // Debugging hidden field value
    // Get user input from CAPTCHA field and trim whitespace

    // Get user input from CAPTCHA field and trim whitespace
    const userCaptchaInputValue = $captchaCodeText.val().trim();

    // Check if CAPTCHA input is empty
    if (!userCaptchaInputValue) {
        // Null or empty CAPTCHA input
        displayCaptchaError("Please enter the CAPTCHA code."); // Display error message for empty input
        return; // Exit the function early to avoid sending an empty CAPTCHA to the server
    }
    // Prepare CAPTCHA validation parameter
    const userCaptchaInput = {
        userCaptchaInput: $captchaCodeText.val() // Pass the user input for CAPTCHA
    };

    // Validate CAPTCHA via AJAX
    $.ajax({
        url: '../ChallanFormView/ValidateCaptcha', // URL to your controller action
        type: 'POST', // Use POST method
        contentType: "application/json;charset=utf-8", // Send as JSON
        data: JSON.stringify(userCaptchaInput), // Convert data to JSON format
        success: function (result) {
            debugger;
            if (result.IsValid) {
                //debugger;
                // Valid CAPTCHA
                document.getElementById("captchaError").innerHTML = " ";

                var $radio = $('input[name=ChallanFromType]:checked');
                var id = $radio.attr('id');
                var urlNew = base_url_service_layer + '/api/Proxy/ChallanForm/ValidateChallanPartial';
                if (id == "GenerateNewChallan") {
                    debugger;
                    challan = getChallanModel();
                    debugger;
                    challanModel = challan;

                    if (isPropertyInfo == false) {
                        challan.propertyInfo.FullAddress = null;
                    }
                    $("#districtDropdownDC").data("kendoDropDownList").value(challan.DistrictId);
                    initializeDropDownWithId(base_url_service_layer + "/api/Proxy/Locations/TehsilsByDistrictId?Id=" + challan.DistrictId, selectTehsilText, "tehsilDropdownDC", challan.TehsilId);
                    //initializeDropDown(base_url_service_layer + "/api/Proxy/Locations/TehsilsByDistrictId?Id=" + id, selectTehsilText, "Tehsil");
                    //$("#tehsilDropdownDC").data("kendoDropDownList").value(challan.TehsilId);
                    //onChangeTotalAmountDeedDetailGenerateChallan();
                    //  urlNew = base_url_service_layer + '/api/Proxy/ChallanForm/ValidateChallanPartial';
                }
                else {
                    debugger;
                    if (id == "PayDeficient") {
                        challanFromDB.numberOfStampPapers = 1;
                    }
                    challan = getChallanModelForDeficient();
                    isMultiplePropertiesExchageOfProperty = challan.isMultiplePropertiesExchageOfProperty;
                    var deficiencyStampDutyOrTTIPVal = $('input[name="DeficencyRadio"]:checked').val();
                    //  alert('defi1')
                    debugger;
                    challan.DeficientType = deficiencyStampDutyOrTTIPVal;
                    if (challan.DeficientType === "TTIP" && challan.LocalGovtId == null) {
                        challan.LocalGovtId = $('#LocalGovtddlReadOnly').val();
                    }
                    challanModel = challan;

                    //urlNew = base_url_service_layer + '/api/Proxy/ChallanForm/ValidateChallanPartialDeedDetailsDeficient';
                }
                isMultiplePropertiesExchageOfProperty = challan.isMultiplePropertiesExchageOfProperty;

                // Submit the Challan model
                $.ajax({
                    url: urlNew,
                    type: 'POST',
                    data: JSON.stringify(challanModel),
                    contentType: "application/json;charset=utf-8",
                    success: function () {
                        debugger;
                        OnNextFirstScreen();
                    },
                    error: function (data) {
                        const response = data.responseText.replace(/"/g, '');
                        $("#purchaserSectionError").css("color", "red");
                        $("#Tehsil_nodataMessage").show().html(response);
                    }
                });
            } else {
                // Handle invalid CAPTCHA
                // Invalid CAPTCHA
                captchaErrorElement.innerHTML = "Invalid CAPTCHA Code."; // Show error message
                captchaErrorElement.style.color = "red"; // Set text color to red
                captchaErrorElement.style.display = "block"; // Make the error message visible

                 refreshCaptcha();
            }
        },
        error: function () {
            // AJAX error
            captchaErrorElement.innerHTML = "Error validating CAPTCHA.";
            captchaErrorElement.style.color = "red";
            captchaErrorElement.style.display = "block"; // Show the error message


        }
    });


}

// Function to refresh the CAPTCHA image
function refreshCaptcha() {
    $.ajax({
        url: '../ChallanFormView/refreshCaptcha', // URL to generate new CAPTCHA
        type: 'GET', // Use GET method
        contentType: "application/json;charset=utf-8",
        cache: false,
        async: true,
        success: function (data) {
            if (data.OperationStatus) {
                console.log(data.AdditionalData);
                $("#CaptchaImage").attr('src', data.AdditionalData.CaptchaImageString); // Update CAPTCHA image
                $("#CapImageText").val(data.AdditionalData.CapImageTextString); // Update hidden field
                $("#CaptchaCode").val(""); // Clear CAPTCHA input field
            } else {
                displayCaptchaError(data.Message); // Show error if CAPTCHA refresh fails
            }
        },
        error: function () {
            displayCaptchaError("Error refreshing CAPTCHA."); // Show error if AJAX fails
        }
    });
}
function CaptchaCheck_google(res) {
    // Get the reCAPTCHA response token from the Google reCAPTCHA widget
    var captchaResponse = grecaptcha.getResponse();

    // Check if the reCAPTCHA is solved
    if (!captchaResponse) {
        document.getElementById("captchaError").innerHTML = "Please solve the CAPTCHA.";
        res = false;
        return res;
    }

    // Prepare parameters for server-side reCAPTCHA validation
    var params = {
        CaptchaResponse: captchaResponse // Token from reCAPTCHA
    };

    // Make an asynchronous request to validate reCAPTCHA on the server
    $.getJSON('../ChallanFormView/CheckCaptcha', params, function (result) {
        if (res) {
            debugger;
            if (result && result.Success === true) {
                document.getElementById("captchaError").innerHTML = "";
                debugger
                // Proceed with challan generation based on the selected type
                var $radio = $('input[name=ChallanFromType]:checked');
                var id = $radio.attr('id');
                var urlNew = base_url_service_layer + '/api/Proxy/ChallanForm/ValidateChallanPartial';

                if (id === "GenerateNewChallan") {
                    challan = getChallanModel();
                    challanModel = challan;

                    if (!isPropertyInfo) {
                        challan.propertyInfo.FullAddress = null;
                    }

                    $("#districtDropdownDC").data("kendoDropDownList").value(challan.DistrictId);
                    initializeDropDown(
                        base_url_service_layer + "/api/Proxy/Locations/TehsilsByDistrictId?Id=" + challan.DistrictId,
                        selectTehsilText,
                        "Tehsil"
                    );
                } else {
                    if (id === "PayDeficient") {
                        challanFromDB.numberOfStampPapers = 1;
                    }
                    challan = getChallanModelForDeficient();
                    challanModel = challan;
                }

                // Submit challan data via an AJAX request
                $.ajax({
                    url: urlNew,
                    type: 'POST',
                    data: JSON.stringify(challanModel),
                    contentType: "application/json;charset=utf-8",
                    success: function (data) {
                        debugger
                        OnNextFirstScreen();
                    },
                    error: function (data) {
                        var response = data.responseText.replace(/"/g, '');
                        $("#purchaserSectionError").css("color", "red");
                        $("#Tehsil_nodataMessage").show();
                        $("#Tehsil_nodataMessage").html(response);
                    }
                });
            } else {
                document.getElementById("captchaError").innerHTML = "Invalid CAPTCHA entered. Please try again.";
                //grecaptcha.reset(); // Reset the reCAPTCHA widget
                res = false;
            }
        } else {
            $("#captchaError").show();
            document.getElementById("captchaError").innerHTML = "Please solve the CAPTCHA again.";
            //grecaptcha.reset(); // Reset the reCAPTCHA widget
            res = false;
        }
    }).fail(function () {
        // Handle validation request failure
        document.getElementById("captchaError").innerHTML = "An error occurred during CAPTCHA validation. Please try again.";
        //grecaptcha.reset(); // Reset the reCAPTCHA widget
        res = false;
    });

    return res;
}


function CaptchaCheck_old(res) {
    // get client-side Captcha object instance
    var captchaObj = $("#CaptchaCode").get(0).Captcha;
    // gather data required for Captcha validation
    var params = {}
    params.CaptchaId = captchaObj.Id;
    params.InstanceId = captchaObj.InstanceId;
    params.UserInput = $("#CaptchaCode").val();
    // make asynchronous Captcha validation request
    $.getJSON('../ChallanFormView/CheckCaptcha', params, function (result) {
        if (res) {
            if (true === result) {
                document.getElementById("captchaError").innerHTML = " ";
                var $radio = $('input[name=ChallanFromType]:checked');
                var id = $radio.attr('id');
                var urlNew = base_url_service_layer + '/api/Proxy/ChallanForm/ValidateChallanPartial';
                if (id == "GenerateNewChallan") {
                    debugger
                    challan = getChallanModel();
                    challanModel = challan;
                    if (isPropertyInfo == false) {
                        challan.propertyInfo.FullAddress = null;
                    }
                    $("#districtDropdownDC").data("kendoDropDownList").value(challan.DistrictId);
                    //initializeDropDownWithId(base_url_service_layer + "/api/Proxy/Locations/BranchesforWhitePaper?Id=" + challan.DistrictId, selectBranchText, "branchDropdownDC", challan.TehsilId);
                    initializeDropDown(base_url_service_layer + "/api/Proxy/Locations/TehsilsByDistrictId?Id=" + id, selectTehsilText, "Tehsil");
                    //$("#tehsilDropdownDC").data("kendoDropDownList").value(challan.TehsilId);
                    //onChangeTotalAmountDeedDetailGenerateChallan();
                    //  urlNew = base_url_service_layer + '/api/Proxy/ChallanForm/ValidateChallanPartial';
                }
                else {
                    if (id == "PayDeficient") {
                        challanFromDB.numberOfStampPapers = 1;
                    }
                    challan = getChallanModelForDeficient();
                    isMultiplePropertiesExchageOfProperty = challan.isMultiplePropertiesExchageOfProperty;
                    challanModel = challan;
                    //urlNew = base_url_service_layer + '/api/Proxy/ChallanForm/ValidateChallanPartialDeedDetailsDeficient';
                }
                isMultiplePropertiesExchageOfProperty = challan.isMultiplePropertiesExchageOfProperty;
                $.ajax({
                    url: urlNew,
                    type: 'POST',
                    data: JSON.stringify(challanModel),
                    contentType: "application/json;charset=utf-8",
                    success: function (data) {
                        OnNextFirstScreen();
                    },
                    error: function (data) {
                        var response = data.responseText.replace(/"/g, '');
                        $("#purchaserSectionError").css("color", "red");
                        $("#Tehsil_nodataMessage").show();
                        $("#Tehsil_nodataMessage").html(data.responseText);
                    }
                });
            }
            else {
                //$('#status').attr('class', 'incorrect');
                //$('#status').text('Check failed');
                document.getElementById("captchaError").innerHTML = InvalidCodeEntered;
                // always change Captcha code if validation fails
               // captchaObj.ReloadImage();
                res = false;
                return res;
            }
        }
        else {
            $("#captchaError").show();
            document.getElementById("captchaError").innerHTML = PleaseEnterAgain;
            // always change Captcha code if validation fails
           // captchaObj.ReloadImage();
            res = false;
            return res;
        }
    }
        );
}

function onTehsilChange() {
    $("#Tehsil_nodataMessage").hide();
    if (challan != null && challan != "" && challan != undefined) {
        LandClassificationChange();
    }
}

function InfoTypeChange() {
    debugger
    var $infoTypeRadio = $('input[name=InfoType]:checked');
    var infoTypeId = $infoTypeRadio.attr('id');
    if (infoTypeId == "Self") {
        $("#AgentFormId").hide();
        $("#Applicant").show();
        ResetTextBox("AgentName");
        ResetTextBox("AgentCnic");
        ResetTextBox("AgentCell");
        ResetTextBox("agentEmail");
        ResetTextBox("AgentCellMasked");

        // Hide masked, show editable
        $("#AgentCellMasked").hide().val("");
        $("#AgentCell").show().val("");

    }
    else if (infoTypeId == "Agent") {
        $("#AgentFormId").show();
        //$("#Applicant").hide();
    }
}



$(document).ready(function () {
    $.get(base_url_service_layer + "/api/Proxy/ChallanForm/GetOtpFlags", function (res) {
        if (res) {
            MutationChallanFeeCutoffDate = new Date(res.MutationChallanFeeCutoffDate);
            // Convert possible "true"/true to Boolean
            EnableMpApi = res.EnableMpApi === true || res.EnableMpApi === "true";
            EnableMpApiChallanSubmit = res.EnableMpApiChallanSubmit === true || res.EnableMpApiChallanSubmit === "true";

            console.log("✅ Flags loaded from ServiceLayer:");
            console.log("EnableMpApi:", EnableMpApi);
            console.log("EnableMpApiChallanSubmit:", EnableMpApiChallanSubmit);
        } else {
            console.warn("⚠️ No response from GetOtpFlags API — defaulting both flags to false.");
        }
    }).fail(function (err) {
        console.error("❌ Failed to fetch OTP flags:", err);
    });
});

function handleSubmitChallan() {
    debugger
    // Parse URL query parameters
    const urlParams = new URLSearchParams(window.location.search);
    const nameParam = urlParams.get('name');
    const agreeParam = urlParams.get('agree');

    console.log("🔍 Current Page Parameters:", {
        name: nameParam,
        agree: agreeParam
    });

    // Check if this is the GenerateChallan page with agree=true
    if (nameParam === "GenerateChallan" && agreeParam === "true") {
        console.log("🔍 On GenerateChallan page → Checking flags:", {
            EnableMpApi,
            EnableMpApiChallanSubmit
        });

        if (EnableMpApi && EnableMpApiChallanSubmit) {
            console.log("✅ Both flags TRUE → Calling SubmitChallan()");
            //SubmitChallan();
            VerifySubmitChallan();
        } else {
            console.log("⚠️ One or both flags FALSE → Calling SubmitChallan()");
            SubmitChallan();
        }
    } else {
        // For all other pages, just call SubmitChallanOld()
        console.log("⚡ Other page → Calling SubmitChallan()");
        SubmitChallan();
    }
}

function SubmitChallan() {
    debugger
            $("#MsgWhitePaperChallan").hide(); 
            $("#waitModalForSave").modal();
            $.ajax({
                type: 'POST',
                url: base_url_service_layer + '/api/Proxy/WhitePaper/AddChallan',
                data: JSON.stringify(challanModel),
                contentType: "application/json;charset=utf-8",
                success: function (data) {
                    debugger
                    console.log("" + data.ChallanNumber);
                    console.log("" + data.PSID);
                    //challanModel.ChallanNumber = data.ChallanNumber;
                    //SendSMS(data);

                    // $("#confirmfrom").hide();
                    $("#confirmfrom").hide();
                    $("#confirmChallan").show();
                    $('#downloadStampNoteDiv').show();
                    $("#MsgWhitePaperChallan").hide();
                    removeArrowBar();
                    createArrowBarLast();
                    
                    printSerial = data.ChallanNumber;
                    printPsid = data.PSID;
                  
                    document.getElementById("serialNum").innerHTML = printSerial;
                    document.getElementById("PsidVal").innerHTML = printPsid;
                   
                    //alert(data.districtName);
                    document.getElementById("districtString").innerHTML = string1MSG +" <span style=\"color:red;\">\""+ data.DistrictString +"\"</span>  "+string2MSG;
                    console.log(printSerial);
                    $("#waitModalForSave").modal('hide');
                    // Reset the form if successfully inserted
                    ResetForm();
                },
                error: function (data) {
                    var response = data.responseText.replace(/"/g, '');
                    //$("#confirmfrom").hide();
                    //$("#confirmChallan").show();
                    alert(response);
                    $("#waitModalForSave").modal('hide');
                    //                    overlayError(response);
                }
            });
        
     
}

function showPSIDBtn() {
    //alert('1')
    if ($('#PSIDChkBox').is(':checked')) {
       // alert('2')
        $("#psidBtn").show();
    } else {
        //alert('3')
        $("#psidBtn").hide();
    }
}
//$('#PSIDChkBox').change(function () {
//    alert('1')
//    if ($(this).is(':checked')) {
//        alert('2')
//        $("#psidBtn").show();
//    } else {
//        alert('3')
//        $("#psidBtn").hide();
//    }
//});

function getPSIDByAPI() {
    debugger
    var testModel;
    var ePaytoken;
   // var time = new Date().getTime(1713775467000); // get your number
    $("#waitModalForSave").modal();
    // Ajax call to get the Saved challan data
    $.ajax({
        type: "GET",
        url: base_url_service_layer + '/api/Proxy/WhitePaper/GetWhitePaperChallan?ChallanNumber=' + $('#serialNum').text(),
        success: function (data) {

            testModel = data;
            var postingObj = {};
            //ePaytoken = localStorage.getItem('ePaytoken');
            var tokenDbExpiryDate = testModel.Token_Expiry_DateTime;
            var chkdate;
            var currentDate = new Date();

            if (tokenDbExpiryDate != null && tokenDbExpiryDate != "") {
                chkdate = new Date(tokenDbExpiryDate);

                if (chkdate >= currentDate) {
                    console.log("valid token;")
                }
                else {
                    console.log("expire token;")
                }
            }
            if (testModel.PSID_Token && chkdate >= currentDate) {//if token is not empty
                if (testModel != null) {

                    postingObj = {
                        deptTransactionId: testModel.ChallanNumber,
                        consumerName: testModel.Party1[0].PersonName,
                        mobileNo: testModel.Party1[0].PersonPhone.replace(/-/g, ''),
                        firstPartyCNIC: testModel.Party1[0].PersonCnic.replace(/-/g, ''),
                        secondPartyCNIC: "",
                        agentCNIC: "",
                        firstPartyName: testModel.Party1[0].PersonName,
                        secondPartyName: "",
                        agentPartyName: "",
                        districtID: testModel.DistrictId,
                        email: testModel.Party1[0].PersonEmail,
                        amountWithinDueDate: 0, // Initialize to 0 and sum up later
                        expiryDate: testModel.challanExpiryDate.split("T")[0],
                        amountBifurcation: []
                    };

                    // Loop through the DutiesApplied array
                    for (let i = 0; i < testModel.DutiesApplied.length; i++) {
                        postingObj.amountBifurcation.push({
                            accountHeadName: testModel.DutiesApplied[i].DutyType,
                            accountNumber: testModel.DutiesApplied[i].DutyAccountHead,
                            amountToTransfer: testModel.DutiesApplied[i].DutyAmount
                        });

                        // Sum up the amountWithinDueDate
                        postingObj.amountWithinDueDate += testModel.DutiesApplied[i].DutyAmount;
                    }
                }
                //testModel.PSID_Token = "eyJhbGciOiJIUzUxMiJ9.eyJzdWIiOiJBQmtoITVSeDRDZCIsImlhdCI6MTcxNTY2ODYyNywiZXhwIjoxNzE2MjczNDI3fQ.Ys7k5Zq24azF_-EzLEyfDClUdygTeFOoVIKa0R8Ha4H5IED1xdSQ5eQwceAkilg33xNtoQ1qBQHToCsAin028g";
                // Ajax call to get PSID using the token
                $.ajax({
                    type: "POST",
                    url: ePay_GetPSID_API_url,
                    data: JSON.stringify(postingObj),
                    contentType: "application/json",  // Specify the content type
                    dataType: "json",  // Specify the expected data type
                    headers: {
                        'Authorization': "Bearer " + testModel.PSID_Token,
                    },

                    success: function (data) {
                        debugger;
                        if (data != null && data.content[0].consumerNumber != null) {
                            var PSID = data.content[0].consumerNumber;
                            var date = new Date(testModel.Token_Expiry_DateTime);
                            date = date.toLocaleString();
                            //$('#PSIDString').text(PSID)
                            document.getElementById("PSIDString").innerHTML = "Your PSID is: " + " <span style=\"color:red;\">\"" + PSID + "\"</span>  ";
                            $('#psidBtn').attr('disabled', true);
                            $("#waitModalForSave").modal('hide');
                            // Ajax call to get the Update the challan data PSID column
                            $.ajax({
                                type: "POST",
                                url: base_url_service_layer + '/api/Proxy/WhitePaper/UpdatePSIDByePayAPI?ChallanNumber=' + $('#serialNum').text() + "&PSID=" + PSID + "&Token=" + testModel.PSID_Token + "&TokenExpiry=" + date,//testModel.Token_Expiry_DateTime,

                                success: function (data) {

                                },
                                error: function (xhr, status, error) {
                                    //alert(error)
                                    console.error(error);
                                }
                            });

                        }
                    },
                    error: function (xhr, status, error) {
                        debugger;
                        var responseText = xhr.responseText;
                        try {
                            var jsonResponse = JSON.parse(responseText);

                            // Check if errors array exists and has at least one error
                            if (jsonResponse.errors && jsonResponse.errors.length > 0) {
                                var errorMessage = jsonResponse.errors[0].message; // Get the message from the first error
                                // Show the error message in an alert
                                alert(errorMessage);
                            } else {
                                // If there are no specific errors, show the general message
                                alert(jsonResponse.message || "An error occurred");
                            }
                        } catch (e) {
                            console.error("Parsing error:", e);
                            // Handle parsing error
                            alert("An unexpected error occurred");
                        }
                    }
                });
            }  //token if ends here

            else {
                alert("An unexpected error occurred");
            }
            
        },
        error: function (xhr, status, error) {
            debugger
            alert(error)
            console.error(error);
        }
    });
}



function getAPIToken() {
    var obj = {
        clientId: "ABkh!5Rx4Cd",
        clientSecretKey: "Xyq1s2iOIZKd1w01!Q6TcZ!9007CVZ5nUlP6RteplBMMnwTePGaehm!6580"
    }

    $.ajax({
        type: "POST",
        url: ePay_GetToken_API_url,
        data: JSON.stringify(obj),
        contentType: "application/json",  // Specify the content type
        dataType: "json",  // Specify the expected data type

        success: function (data) {
            if (data != null && data.content[0].token.accessToken != null) {
                var accessToken = data.content[0].token.accessToken;
            }
            return accessToken;
        },
        error: function (xhr, status, error) {
           // alert(error)
            console.error(error);
        }
    });
}

function getPSIDByAPI1() {
   // alert('4')
    var challanNumber = $('#serialNum').text();
    $.ajax({
        url: base_url_service_layer + '/api/Proxy/WhitePaper/GetPSIDByePayAPI?ChallanNumber=' + challanNumber,
        type: 'POST',
        contentType: "application/json;charset=utf-8",
        //async: false,
        success: function (data) {
           // alert('5')
            //alert(data)
            document.getElementById("PSIDString").innerHTML = "Your PSID is: " + " <span style=\"color:red;\">\"" + data + "\"</span>  ";

            //$("#PSIDString").val("12345")
        },
        error: function (data) {

        }
    });
}
//function showPSIDBtn() {
//    if()
//    $("#challanform").show();
//}
function BackButtonToChallanForm() {
    $("#challanform").show();
    refreshCaptcha();
   //grecaptcha.reset(); 
    //var captchaObj = $("#CaptchaCode").get(0).Captcha;
    //captchaObj.ReloadImage();
    $("#confirmfrom").hide();

    removeArrowBarFirstScreen();
    createArrowBarFirstScreen();
}
function BackButtonToCopyingFeeAmount() {

    $("#CopyingFeeAmount").show();
    refreshCaptcha();
   //grecaptcha.reset(); 
    //var captchaObj = $("#CaptchaCode").get(0).Captcha;
    //captchaObj.ReloadImage();
    $("#confirmfrom").hide();

    removeArrowBar();
    createArrowBar();
}
function ResetForm() {
    $(".k-invalid-msg").hide();
    ResetTextBox("AgentName");
    ResetTextBox("AgentCnic");
    ResetTextBox("AgentCell");
    ResetTextBox("agentEmail");
    ResetTextBox("oldRegistryNumber");

    ResetTextBox("noOfStampsValue");
    ResetTextBox("denominationValue");
    ResetPersonForm();
    ResetTextBox("PersonPhone");
    ResetTextBox("RelationName");
    ResetTextBox("PersonName");
    ResetTextBox("PersonCnic");
    ResetTextBox("PersonAddress");

    
    ResetTextBox("Denomination");
    ResetTextBox("Reason");

    $("#District").val("").data("kendoDropDownList").text(selectDistrictText);
    $("#Tehsil").val("").data("kendoDropDownList").text(selectTehsilText);
    //$("#Branch").val("").data("kendoDropDownList").text(selectBranchText);
    $("#Purpose").val("").data("kendoDropDownList").text(selectPurposeText);
        
    $("#Relation").val("").data("kendoDropDownList").text(selectRelationText);

    initializeDropDown(base_url_service_layer + '/api/Proxy/Locations/AllDistrictsForWhitePaper', selectDistrictText, "District");
    initializeDropDown(base_url_service_layer + '/api/Proxy/Locations/AllDistrictsForWhitePaper', selectDistrictText, "districtDropdownDC");
    //initializeDropDown(base_url_service_layer + '/api/Proxy/Locations/BranchesforWhitePaper?id=', selectBranchText, "Branch");
    //initializeDropDown(base_url_service_layer + '/api/Proxy/Locations/BranchesforWhitePaper?id=', selectBranchText, "branchDropdownDC");
    initializeDropDown(base_url_service_layer + '/api/Proxy/Locations/TehsilsByDistrictId?id=', selectTehsilText, "Tehsil");
    initializeDropDown(base_url_service_layer + '/api/Proxy/Locations/TehsilsByDistrictId?id=', selectTehsilText, "tehsilDropdownDC");
    initializeDropDown(base_url_service_layer + '/api/Proxy/Locations/GetPurposeForWhitePaper', selectPurposeText, "Purpose");
    initializeDropDown(base_url_service_layer + "/api/Proxy/Lookup/LookupByCategory?category=Relation Type", selectRelationText, "Relation");/*"Select Relationship", "Relation");*/
    $("#Relation option:first").val("0");
    //refreshCaptcha();
   //grecaptcha.reset(); 
    //var captchaObj = $("#CaptchaCode").get(0).Captcha;
    //captchaObj.ReloadImage();
    challanFromDB = {};
        
    challan = {};
    challanModel = {};
}

function ResetTextBox(id) {
    $("#" + id).val("");
    $("#" + id).removeClass("empty");
    $("#" + id).addClass("empty");
}
      
function ResetDropDown(field) {
       
    $("#" + field).data("kendoDropDownList").value("");
    //$(".k-input").html("Select Relation");
}

function SetDropDownValue(field, value) {
    //debugger;
    //$("#" + field).val(value);
    //if (updateFlag) {
    //    $("#Relation option:first").removeAttr("selected");
    //    //alert($("#Relation option:selected").html());
    //    $(".k-input").html($("#Relation option:selected").html());
    //}
    //else {
    //    $(".k-input").html("Select Relation");
    //}
    if ($("#popupCheck").val() == "1") { $("#popupCheck").val("0"); }
    else {
        $("#" + field).val(value);
    }
}
  
function changeRelation() {
        
    var value = $("#Relation").data("kendoDropDownList").text();
    $("#personForm > div:nth-child(2) > div:nth-child(2) > div > div > div > div").html(value);
    $("#lastSelecetedRelation").val($("#Relation").val());
}
  
function setTextBoxValue(field, value) {
    $("#" + field).val(value);
    $("#" + field).removeClass("empty");
}

//Hamza New Code Start Here..
function CheckCNIC(type) {
    debugger;
    var number = "";
    var contactField = "";
    var cnicField = "";
    var cell = "";

    if (type == "person") {
        contactField = "PersonPhone";
        cnicField = "PersonCnic";
        cell = $("#PersonPhone").val();
        number = $("#PersonCnic").val();
    }
    else if (type == "agent") {
        contactField = "AgentCell";
        cnicField = "AgentCnic";
        cell = $("#AgentCell").val();
        number = $("#AgentCnic").val();
    }

    if (cell.length > 1) {
        type = type.charAt(0).toUpperCase() + type.slice(1);
        //verify(contactField, cnicField, type)
        verifyWhitePaper(contactField, cnicField, type)
    }
    if (!isValidCNIC(number)) {
        if (type == "person") {
            $("#PersonPhone").val('');
            $("#PersonCnic").val('').focus();
            $("#CNICNo_validationMessage").show();
        }
        else if (type == "agent") {
            $("#AgentCell").val('');
            $("#AgentCnic").val('').focus();
            $("#AgentCNICNo_validationMessage").show();

        }
    } else {
        if (type == "person") {
            $("#CNICNo_validationMessage").hide();
        }
        else if (type == "agent") {

            $("#AgentCNICNo_validationMessage").hide();
        }
    }

    $.ajax({
        url: base_url_service_layer + '/api/Proxy/CitizenPortal/CheckCNIC?Cnic=' + number,
        type: 'POST',
        contentType: "application/json;charset=utf-8",
        async: false,
        success: function (data) {
            if (data.result == true) {

                if (type == "person") {
                    $("#CNICNo_validationMessage").show();
                }
                else if (type == "agent") {
                    $("#AgentCNICNo_validationMessage").show();
                }

                //$("#PersonPhone_validationMessage").show();
                //var validator = $("#personForm").kendoValidator().data("kendoValidator");
                //validator.showMessage();
                //if (!validator.validateInput($("#PersonPhone"))) {
                //    alert("UserName is not valid!");
                //} else {
                //    alert("UserName is valid!");
                //}


            }
            else {
                if (type == "person") {
                    $("#CNICNo_validationMessage").hide();
                }
                else if (type == "agent") {
                    $("#AgentCNICNo_validationMessage").hide();
                }

            }
        },
        error: function (data) {

        }
    });
}

function onFocusOutValidateForJudicial() {
    debugger;

    var infoTypeId = $('input[name=InfoType]:checked').attr('id');

    // Reset pehle
    $('#PersonCnic').removeAttr("required");
    $('#AgentCnic').removeAttr("required");

    $("#PersonCNICNo_validationMessage").hide();
    $("#AgentCNICNo_validationMessage").hide();

    if (infoTypeId === "Self") {
        // Self case
        $('#PersonCnic').attr("required", "required");

        // Agent fields optional
        $('#AgentCnic').removeAttr("required");
    }
    else if (infoTypeId === "Agent") {
        // Agent case
        $('#AgentCnic').attr("required", "required");

        // Person fields optional
        $('#PersonCnic').removeAttr("required");
    }
}

$("#PersonCnic").focusout(function () {
    onFocusOutValidateForJudicial();
    //CheckMobileNumber()
});

//Hamza New Code End Here..
      
function CheckCNICOld(type) {
    var number = "";
    if (type == "person") {
        number = $("#PersonCnic").val();
    }
    else if (type == "agent") {
        number = $("#AgentCnic").val();
    } 
    $.ajax({
        url: base_url_service_layer + '/api/Proxy/CitizenPortal/CheckCNIC?Cnic=' + number,
        type: 'POST',
        contentType: "application/json;charset=utf-8",
        async: false,
        success: function (data) {
            if (data.result == true) {

                if (type == "person") {
                    $("#CNICNo_validationMessage").show();
                    $("#CNICNo_validationMessage").html("<span class='k-icon k-warning' style='margin-right: 3px;'> </span> " + CNICnotvalid);
                }
                else if (type == "agent") {
                    $("#AgentCNICNo_validationMessage").show();
                    $("#AgentCNICNo_validationMessage").html("<span class='k-icon k-warning' style='margin-right: 3px;'> </span> " + AgentCNICnotvalid);

                }
            }
            else {
                if (type == "person") {
                    $("#CNICNo_validationMessage").hide();
                }
                else if (type == "agent") {
                    $("#AgentCNICNo_validationMessage").hide();
                }
                    
            }
        },
        error: function (data) {

        }
    });
}

function isValid() {
    debugger;
    var res = true;

    // Get which radio button is checked
    var infoTypeId = $('input[name=InfoType]:checked').attr('id');

    // --- Always validate dealform + registryForm ---
    if (!$('#dealform').kendoValidator().data('kendoValidator').validate()) res = false;
    if (!$('#registryForm').kendoValidator().data('kendoValidator').validate()) res = false;

    // --- Validate depending on radio selection ---
    if (infoTypeId === "Self") {
        // Person form only
        if (!$('#personForm').kendoValidator().data('kendoValidator').validate()) res = false;

        if ($("#PersonCnic").val().includes("_")) {
            $("#CNICNo_validationMessage").show().html("CNIC is incomplete");
            res = false;
        }
        if ($("#PersonPhone").val().includes("_")) {
            $("#ContactNo_validationMessage").show().html("Contact Number is incomplete");
            res = false;
        }

    } else if (infoTypeId === "Agent") {
        // Agent form
        if (!$('#agentForm').kendoValidator().data('kendoValidator').validate()) res = false;

        if ($("#AgentCnic").val().includes("_")) {
            $("#AgentCNICNo_validationMessage").show().html("CNIC is incomplete");
            res = false;
        }
        if ($("#AgentCell").val().includes("_")) {
            $("#AgentContactNo_validationMessage").show().html("Contact Number is incomplete");
            res = false;
        }

        // Person form (mandatory when Agent selected)
        if (!$('#personForm').kendoValidator().data('kendoValidator').validate()) res = false;

        if ($("#PersonCnic").val().includes("_")) {
            $("#CNICNo_validationMessage").show().html("CNIC is incomplete");
            res = false;
        }
        if ($("#PersonPhone").val().includes("_")) {
            $("#ContactNo_validationMessage").show().html("Contact Number is incomplete");
            res = false;
        }
    }

    // Form errors
    if (!res) return false;

    // OTP VERIFICATION CHECK

        if (infoTypeId === "Self") {
            var selfVerified = $("#PersonPhone").attr("data-verified") === "true";

            if (!selfVerified) {
                if (otpTotalSeconds > 0) {
                    $("#OTPInputModal").modal("show");
                } else {
                    verifyWhitePaper("PersonPhone", "PersonCnic", "Person");
                }
                return false; // Next screen block
            }

        } else if (infoTypeId === "Agent") {
            var agentVerified  = $("#AgentCell").attr("data-verified") === "true";
            var personVerified = $("#PersonPhone").attr("data-verified") === "true";

            if (!agentVerified) {
                if (otpTotalSeconds > 0) {
                    $("#OTPInputModal").modal("show");
                } else {
                    verifyWhitePaper("AgentCell", "AgentCnic", "Agent");
                }
                return false;
            }

            if (!personVerified) {
                if (otpTotalSeconds > 0) {
                    $("#OTPInputModal").modal("show");
                } else {
                    verifyWhitePaper("PersonPhone", "PersonCnic", "Person");
                }
                return false;
            }
        }
    // END OTP CHECK

    // --- Captcha required check ---
    if ($("#CaptchaCode").val() === "") {
        refreshCaptcha();
        $("#captchaError").show().html("Captcha is required").css("color", "red");
        return false;
    }

    if (!res) return false;

    $('#status').attr('class', 'inProgress').text('Checking...');

    // --- Captcha validation AJAX ---
    const captchaErrorElement = document.getElementById("captchaError");
    const userCaptchaInputValue = $("#CaptchaCode").val().trim();

    if (!userCaptchaInputValue) {
        refreshCaptcha();
        captchaErrorElement.innerHTML = "Please enter the CAPTCHA code.";
        captchaErrorElement.style.color = "red";
        captchaErrorElement.style.display = "block";
        return false;
    }

    $.ajax({
        url: '../ChallanFormView/ValidateCaptcha',
        type: 'POST',
        contentType: "application/json;charset=utf-8",
        data: JSON.stringify({ userCaptchaInput: userCaptchaInputValue }),
        success: function (result) {
            debugger
            if (result.IsValid) {

                challanModel = getChallanModel();
                renderChallanForWhitePaper(challanModel);

                //if (!isCNIC_ContactVerified()) { //Code Comment..
                //    return;
                //}

                //if (!VerifySubmitChallan()) { //Code Comment..
                //    return;
                //}

                $("#captchaError").hide().html("");
                $("#challanform").hide();
                $("#confirmfrom").show();
                
                removeArrowBar();
                createArrowBar();

                $('html, body').scrollTop(0);
            } else {
                captchaErrorElement.innerHTML = "Invalid CAPTCHA Code.";
                captchaErrorElement.style.color = "red";
                captchaErrorElement.style.display = "block";
            }
        },
        error: function () {
            captchaErrorElement.innerHTML = "Error validating CAPTCHA.";
            captchaErrorElement.style.color = "red";
            captchaErrorElement.style.display = "block";
            refreshCaptcha();
        }
    });

    return res;
}


    //else {
    //    debugger;

    //    // Get the reCAPTCHA response token from the Google reCAPTCHA widget
    //    var captchaResponse = grecaptcha.getResponse();

    //    // Check if the reCAPTCHA is solved
    //    if (!captchaResponse) {
    //        $("#captchaError").show();
    //        document.getElementById("captchaError").innerHTML = "Please solve the CAPTCHA.";
    //        res = false;
    //        return res;
    //    }

    //    // Prepare parameters for server-side CAPTCHA validation
    //    var params = {
    //        CaptchaResponse: captchaResponse // Token from Google reCAPTCHA
    //    };

    //    // Make asynchronous request to validate reCAPTCHA on the server
    //    $.getJSON('../ChallanFormView/CheckCaptcha', params, function (result) {
    //        console.log("Result : " + result);

    //        if (result && result.Success === true) {
    //            $("#captchaError").show();
    //            document.getElementById("captchaError").innerHTML = "";

    //            $("#challanform").hide();
    //            $("#confirmfrom").show();
    //            challanModel = getChallanModel();
    //            renderChallanForWhitePaper(challanModel);
    //            removeArrowBar();
    //            createArrowBar();

    //            // Scroll to the top of the page
    //            $('html, body').scrollTop(0);
    //        } else {
    //            $("#captchaError").show();
    //            document.getElementById("captchaError").innerHTML = "Invalid CAPTCHA entered. Please try again.";
    //            //grecaptcha.reset(); // Reset the reCAPTCHA widget
    //            res = false;
    //        }
    //    }).fail(function () {
    //        // Handle validation request failure
    //        $("#captchaError").show();
    //        document.getElementById("captchaError").innerHTML = "An error occurred during CAPTCHA validation. Please try again.";
    //        //grecaptcha.reset(); // Reset the reCAPTCHA widget
    //        res = false;
    //    });
    //}
    //    console.log("Final : " + res);
    //    return res;
    //}







//    else {
//        debugger;
//        // gather data required for Captcha validation
//        var params = {}
//        params.CaptchaId = captchaObj.Id;
//        params.InstanceId = captchaObj.InstanceId;
//        params.UserInput = $("#CaptchaCode").val();

//        // make asynchronous Captcha validation request
//        $.getJSON('../ChallanFormView/CheckCaptcha', params, function (result) {
//            console.log("Result : " + result);
//            if (true === result) {
//                $("#captchaError").show();
//                document.getElementById("captchaError").innerHTML = "";

//                $("#challanform").hide();
//                $("#confirmfrom").show();
//                challanModel = getChallanModel();
//                renderChallanForWhitePaper(challanModel);
//                removeArrowBar();
//                createArrowBar();
//                if ($('body').scrollTop() > 0) {
//                    $('body').scrollTop(0);         //Chrome,Safari
//                } else {
//                    if ($('html').scrollTop() > 0) {    //IE, FF
//                        $('html').scrollTop(0);
//                    }
//                } 
                    
//            } else {
//                $("#captchaError").show();
//                document.getElementById("captchaError").innerHTML = InvalidCodeEntered;
//                // always change Captcha code if validation fails
//                captchaObj.ReloadImage();
//                res = false;
//            }
//        });
//    }
//    console.log("Final : "+res);
//    return res;
//}
/////////////////////////////////////////////
//View initializing functions
////////////////////////////////////////////

$(function () {
    var container = $("#agentForm");
    kendo.init(container);
    container.kendoValidator({
        rules: {
            validmask: function (input) {
                if (input.is("[data-validmask-msg]") && input.val() != "") {
                    var maskedtextbox = input.data("kendoMaskedTextBox");
                    return maskedtextbox.value().indexOf(maskedtextbox.options.promptChar) === -1;
                }

                return true;
            }
        }
    });
});

function UrduValidationMessage(Id)
{
    $(Id).kendoValidator({
        validateOnBlur: true,
        messages: {
            required: "{0} ضروری ہے", pattern: "{0} درست نہیں ہے", min: "{0} should be greater than or equal to {1}", max: "{0} should be smaller than or equal to {1}", step: "{0} is not valid", email: "{0} درست ای میل نہیں ہے", url: "{0} درست نہیں ہے URL", date: "{0} درست تاریخ نہیں ہے"
        }
    });
}

function InitializeValidators() {
       
    if (lang == 'ur') {

        UrduValidationMessage("#dealform");
        UrduValidationMessage("#agentForm");
        UrduValidationMessage("#propertyAddressForm");
        UrduValidationMessage("#constructedStructureFormSecond");
        UrduValidationMessage("#constructedStructureForm");
        UrduValidationMessage("#PropertyForm");
        UrduValidationMessage("#NonPropertyForm");
        UrduValidationMessage("#ConstructedForm");
        UrduValidationMessage("#ClassificationForm");
        UrduValidationMessage("#DeedDetailsForDeficientFormSuitFor");
        UrduValidationMessage("#DeedDetailsForDeficientFormProperty");
        UrduValidationMessage("#DeedDetailsForDeficientFormProperty2");
        UrduValidationMessage("#DeficientAmountForm");
        UrduValidationMessage("#leaseDeedPayCVTRegistrationForm");
        UrduValidationMessage("#RegistrationPayCVTandRegDeedDetailsForm");
        UrduValidationMessage("#registryFeeDiv");
        UrduValidationMessage("#RegistrationPayCVTandRegDeedDetailsFormDeficient");
        UrduValidationMessage("#DeedDetailsForGenerateChallanFormNonJudicialForm");
        UrduValidationMessage("#DeedDetailsForGenerateChallanFormNonJudicialSecondForm");
        UrduValidationMessage("#DeedDetailsForGenerateChallanFormNonJudicialReadOnly");
        UrduValidationMessage("#DeedDetailsForGenerateChallanFormNonJudicialSecond");
        UrduValidationMessage("#DeedDetailsForGenerateChallanFormJudicial");
        UrduValidationMessage("#DeedDetailsForGenerateChallanFormJudicial");
        UrduValidationMessage("#ConstructedAreaCVTForm");
        UrduValidationMessage("#landClassificationCVTForm");
        UrduValidationMessage("#PayCVTFormDeficient");
        UrduValidationMessage("#LandAreaCVTForm");
        UrduValidationMessage("#payableCVTExchagneOfPropertyForm");        
           

    }
    else {
        $("#dealform").kendoValidator({
            validateOnBlur: true
        });


        $("#dealform").kendoValidator({
            validateOnBlur: true
        });

        $("#propertyAddressForm").kendoValidator({
            validateOnBlur: true

        });

        $("#constructedStructureFormSecond").kendoValidator({
            validateOnBlur: true
        });
        $("#constructedStructureForm").kendoValidator({
            validateOnBlur: true
        });
        $("#PropertyForm").kendoValidator({
            validateOnBlur: true
        });

        $("#NonPropertyForm").kendoValidator({
            validateOnBlur: true
        });
        $("#ConstructedForm").kendoValidator({
            validateOnBlur: true
        });

        $("#ClassificationForm").kendoValidator({
            validateOnBlur: true
        });
        $("#property2").kendoValidator({
            validateOnBlur: true

        });
        $("#AmountDiv").kendoValidator({
            validateOnBlur: true

        });
        ///////////////////Deed Details Deficient Screen///////////
        $("#DeedDetailsForDeficientFormSuitFor").kendoValidator({
            validateOnBlur: true

        });
        $("#DeedDetailsForDeficientFormProperty").kendoValidator({
            validateOnBlur: true

        });

        $("#DeedDetailsForDeficientFormProperty2").kendoValidator({
            validateOnBlur: true

        });

        $("#DeficientAmountForm").kendoValidator({
            validateOnBlur: true

        });
        ///////////////////Deed Details Pay CVT and Registration Fee///////////
        //$("#DeedDetailsForPayCVTandRegFormProperty").kendoValidator({
        //    validateOnBlur: true

        //}); 
        $("#leaseDeedPayCVTRegistrationForm").kendoValidator({
            validateOnBlur: true

        });

        $("#RegistrationPayCVTandRegDeedDetailsForm").kendoValidator({
            validateOnBlur: true

        });
        $("#registryFeeDiv").kendoValidator({
            validateOnBlur: true

        });
        $("#RegistrationPayCVTandRegDeedDetailsFormDeficient").kendoValidator({
            validateOnBlur: true

        });
        ///////////////////Deed Details Generate Challan Screen///////////
        $("#DeedDetailsForGenerateChallanFormNonJudicialForm").kendoValidator({
            validateOnBlur: true

        });

        $("#DeedDetailsForGenerateChallanFormNonJudicialSecondForm").kendoValidator({
            validateOnBlur: true

        });

        $("#DeedDetailsForGenerateChallanFormNonJudicialReadOnly").kendoValidator({
            validateOnBlur: true

        });



        $("#DeedDetailsForGenerateChallanFormNonJudicialSecond").kendoValidator({
            validateOnBlur: true

        });
        $("#DeedDetailsForGenerateChallanFormJudicial").kendoValidator({
            validateOnBlur: true

        });
        $("#DeedDetailsForGenerateChallanFormJudicial").kendoValidator({
            validateOnBlur: true

        });
        //$("#LeaseForm").kendoValidator({
        //    validateOnBlur: true

        //});

        //$("#premiumForm").kendoValidator({
        //    validateOnBlur: true

        //});

        //$("#LeasePeriodForGenerateChallanFormNonJudicial").kendoValidator({
        //    validateOnBlur: true

        //});
        //$("#LeaseMoneyForGenerateChallanFormNonJudicial").kendoValidator({
        //    validateOnBlur: true

        //});
        //////////////////////////////////////CVT Screen///////////////
        $("#ConstructedAreaCVTForm").kendoValidator({
            validateOnBlur: true

        });
        $("#landClassificationCVTForm").kendoValidator({
            validateOnBlur: true

        });
        $("#PayCVTFormDeficient").kendoValidator({
            validate: true
        });

        $("#LandAreaCVTForm").kendoValidator({
            validate: true
        });

        $("#payableCVTExchagneOfPropertyForm").kendoValidator({
            validate: true
        });

        // $("#PayableCVTForm").kendoValidator({
        //validate: true

        // });
    }
}

function getUrlVars() {
    var vars = [], hash;
    var hashes = window.location.href.slice(window.location.href.indexOf('?') + 1).split('&');
    for (var i = 0; i < hashes.length; i++) {
        hash = hashes[i].split('=');
        vars.push(hash[0]);
        vars[hash[0]] = hash[1];
    }
    return vars;
}

function moveCaretToStart(el) {
    if (typeof el.selectionStart == "number") {
        el.selectionStart = el.selectionEnd = 0;
    } else if (typeof el.createTextRange != "undefined") {
        el.focus();
        var range = el.createTextRange();
        range.collapse(true);
        range.select();
    }
}
  
function TehsilBlur() {
    $("#Tehsil_nodataMessage").hide();
}

function BranchBlur() {
    $("#Branch_nodataMessage").hide();
}

function validateTehsil() {
    var value = $("#District").data("kendoDropDownList").text();
    if (value == selectDistrictText) {
        $("#Tehsil").kendoValidator().data("kendoValidator").hideMessages();
        $("#Tehsil_nodataMessage").html("<span class='k-icon k-warning' style='margin-right: 3px;'> </span> " + SelectDistrictFirst);
        $("#Tehsil_nodataMessage").show();
        return false;
    } else {
        $("#Tehsil_nodataMessage").hide();
    }
    return true;
}

function validateBranch() {
    var value = $("#District").data("kendoDropDownList").text();
    if (value == selectDistrictText) {
        $("#Branch").kendoValidator().data("kendoValidator").hideMessages();
        $("#Branch_nodataMessage").html("<span class='k-icon k-warning' style='margin-right: 3px;'> </span> " + SelectDistrictFirst);
        $("#Branch_nodataMessage").show();
        return false;
    } else {
        $("#Branch_nodataMessage").hide();
    }
    return true;
}

$(document).ready(function () {
    $("#AgentCNICNo_validationMessage").hide();
    $("#AgentContactNo_validationMessage").hide();
    //$("#psidBtn").hide()

    $("#AgentFormId").hide();
    $("#Applicant").show();
    console.log('Form is being loading ..');
    challan = null;
   // document.getElementById("nextFirstScreenButton").disabled = false;
    var textBox = document.getElementById("AgentCnic");
       
    $(".RemoveMask").kendoMaskedTextBox({
        mask: "00000-0000000-0",
        clearPromptChar: true,
    });
        
    $("#AgentCell").kendoMaskedTextBox({
        mask: "0000-0000000",//"\\0300-0000000",
        clearPromptChar: true
    });

    $('#PersonAddress').on('keypress', function (event) {
        var regex = new RegExp("^[a-zA-Z0-9,./\\-()\\d\\s]+$");
        var key = String.fromCharCode(!event.charCode ? event.which : event.charCode);
        if (!regex.test(key)) {
            event.preventDefault();
            return false;
        }
    });
    
    $("#PersonCnic").kendoMaskedTextBox({
        mask: "00000-0000000-0",
        clearPromptChar: true
    });


    $("#PersonPhone").kendoMaskedTextBox({
        mask: "0000-0000000",//"\\0300-0000000",
        clearPromptChar: true
    });

    $('#AgentCnic').on('blur', function (event) {
        debugger;
        if (($("#AgentCnic").val()).includes("_")) {
            $("#AgentCnic").kendoValidator().data("kendoValidator").hideMessages();
            $("#AgentCNICNo_validationMessage").css("display", "block");
            $("#AgentCNICNo_validationMessage").html("<span class='k-icon k-warning' style='margin-right: 3px;'> </span> " + CNICIsIncomplete);
            return false;
        } else {
            $("#AgentCNICNo_validationMessage").css("display", "none");
            if ($("#AgentCnic").val() != "") {
                CheckCNIC('agent');
            }
        }
        return true;
    });
    
    $('#AgentCell').on('blur', function (event) {
        if (($("#AgentCell").val()).includes("_")) {
            $("#AgentCell").kendoValidator().data("kendoValidator").hideMessages();
            $("#AgentContactNo_validationMessage").css("display", "block");
            $("#AgentContactNo_validationMessage").html("<span class='k-icon k-warning' style='margin-right: 3px;'> </span> " + ContactNoIsInComplete);
            return false;
        } else {
            $("#AgentContactNo_validationMessage").css("display", "none");
        }
        return true;
    });

    $('#PersonCnic').on('blur', function (event) {
        debugger;
        if (($("#PersonCnic").val()).includes("_")) {
            $("#PersonCnic").kendoValidator().data("kendoValidator").hideMessages();
            $("#CNICNo_validationMessage").css("display", "block");
            $("#CNICNo_validationMessage").html("<span class='k-icon k-warning' style='margin-right: 3px;'> </span> " + CNICIsIncomplete);
            return false;
        } else {
            $("#CNICNo_validationMessage").css("display", "none");
            if($("#PersonCnic").val() != ""){
                CheckCNIC('person');
            }
        }
        return true;
    });
    $('#PersonPhone').on('blur', function (event) {
        if (($("#PersonPhone").val()).includes("_")) {
            $("#PersonPhone").kendoValidator().data("kendoValidator").hideMessages();
            $("#ContactNo_validationMessage").css("display", "block");
            $("#ContactNo_validationMessage").html("<span class='k-icon k-warning' style='margin-right: 3px;'> </span> " + ContactNoIsInComplete);
            return false;
        } else {
            $("#ContactNo_validationMessage").css("display", "none");
        }
        return true;
    });
    
    $(function () {
        var container = $("#personForm");
        kendo.init(container);
        container.kendoValidator({
            rules: {
                validmask: function (input) {
                    console.log(input);
                    if (input.is("[data-validmask-msg]") && input.val() != "") {
                        var maskedtextbox = input.data("kendoMaskedTextBox");
                        return maskedtextbox.value().indexOf(maskedtextbox.options.promptChar) === -1;
                    }

                    return true;
                },
                   
            }
        });
        var lang = '@System.Globalization.CultureInfo.CurrentCulture.Name';
        if (lang == 'ur') {
            $("#personForm").kendoValidator({
                validateOnBlur: true,
                messages: {
                    required: "{0} ضروری ہے", pattern: "{0} درست نہیں ہے", min: "{0} should be greater than or equal to {1}", max: "{0} should be smaller than or equal to {1}", step: "{0} is not valid", email: "{0} درست ای میل نہیں ہے", url: "{0} درست نہیں ہے URL", date: "{0} درست تاریخ نہیں ہے"
                }
            });
        }
    });

    var container = $("#registryForm");
    kendo.init(container);
    container.kendoValidator({
        rules: {
            validmask: function (input) {
                console.log(input);
                if (input.is("[data-validmask-msg]") && input.val() != "") {
                    var maskedtextbox = input.data("kendoMaskedTextBox");
                    return maskedtextbox.value().indexOf(maskedtextbox.options.promptChar) === -1;
                }

                return true;
            }
        }
    });
      
    document.getElementById("PersonCnic").classList.remove("k-textbox");
    document.getElementById("PersonPhone").classList.remove("k-textbox");

    $(".RemoveMask").focus(function () {
        var id = $(this).attr("id");
            
        var value = $(this).val();
        if (value == "_____-_______-_" || value == "_____-_______-_") {
            $(this).val("");
        }
        else {
            $(this)[0].focus();
        }
    });

    $('#PersonCnic').focus(function () {

        var id = $(this).attr("id");

        var value = $(this).val();
        if (value == "_____-_______-_" || value == "_____-_______-_") {
            $(this).val("");
        }
        else {
            $(this)[0].focus();
        }

    });

    $('#AgentCnic').focus(function () {
        ;
        var id = $(this).attr("id");

        var value = $(this).val();
        if (value == "_____-_______-_" || value == "_____-_______-_") {
            $(this).val("");
        }
        else {
            $(this)[0].focus();
        }

    });

    $('#PersonPhone').focus(function () {

        var id = $(this).attr("id");

        var value = $(this).val();
        //console.log(value);

        if (value == "____-_______") {
            $(this).val("");
        }
        else {
            $(this)[0].focus();
        }

    });

    $('.alphaonly').bind('keyup blur', function (evt) {
        var node = $(this);
            
        var code = evt.which ? evt.which : event.keyCode;

        // 37 = left arrow, 39 = right arrow.
        if(code != 37 && code != 39)
            //$(evt).val($(evt).val().replace(/[^A-Za-z0-9]/g, ' '))\
            node.val(node.val().replace(/[^a-zA-Z\s]/g, ''));

            //"[A-Za-z ]+"
    });

    $('[data-toggle="tooltip"]').tooltip();

    if ($("#ContactNo").text() == null || $("#ContactNo").text() == "") {
        $("#ContactNo_validationMessage").hide();
        $('#ContactNo').removeAttr("required");
    }

    var window = $("#deleteWin").kendoWindow({

        visible: false, //the window will not appear before its .open method is called
        width: "400px",
        height: "100px",
    }).data("kendoWindow");
    document.getElementById("AgentCnic").classList.remove("k-textbox");
    document.getElementById("AgentCell").classList.remove("k-textbox");
    $("#confirmfrom").hide();
    $("#confirmChallan").hide();
    // $("#window").data("kendoWindow").close();
    initializeDropDown(base_url_service_layer + '/api/Proxy/Locations/AllDistrictsForWhitePaper', selectDistrictText, "District");
    initializeDropDown(base_url_service_layer + '/api/Proxy/Locations/AllDistrictsForWhitePaper', selectDistrictText, "districtDropdownDC");
    //initializeDropDown(base_url_service_layer + '/api/Proxy/Locations/BranchesforWhitePaper?id=', selectBranchText, "Branch");
    //initializeDropDown(base_url_service_layer + '/api/Proxy/Locations/BranchesforWhitePaper?id=', selectBranchText, "branchDropdownDC");
    initializeDropDown(base_url_service_layer + '/api/Proxy/Locations/TehsilsByDistrictId?id=', selectTehsilText, "Tehsil");
    initializeDropDown(base_url_service_layer + '/api/Proxy/Locations/TehsilsByDistrictId?id=', selectTehsilText, "tehsilDropdownDC");
    initializeDropDown(base_url_service_layer + '/api/Proxy/Locations/GetPurposeForWhitePaper', selectPurposeText, "Purpose");
    
    //Added By Nadeem:
    initializeDropDown(base_url_service_layer + "/api/Proxy/Lookup/LookupByCategory?category=Relation Type", selectRelationText, "Relation");/*"Select Relationship", "Relation");*/

    InitializeValidators();
    //fillData();
    // $("#Branch").data("kendoDropDownList").text(selectBranchText);
    $("#Tehsil").data("kendoDropDownList").text(selectTehsilText);
    // ToolTips for form 
    $("#tooltip_AgentName").kendoTooltip({
        content: "Required. Name of the Agent",
        position: "top"
    });
		$("#tooltip_cnic").kendoTooltip({
        content: "Required. CNIC of the Agent",
        position: "top",
    });
    $("#tooltip_AgentCell").kendoTooltip({
        content: "Required. Contact No. of the Agent",
        position: "top"
    });
    $("#tooltip_agentEmail").kendoTooltip({
        content: "Email of the Agent",
        position: "top"
    });
    $("#tooltip_District").kendoTooltip({
        content: "Required. District from where stamp paper would be issued",
        position: "top"
    });

    $("#tooltip_Tehsil").kendoTooltip({
        content: "required. Tehsil from where stamp paper would be issued",
        position: "top"
    });
      
    $("#tooltip_PersonName").kendoTooltip({
        content: "Required. Name of Person involved",
        position: "top"
    });
    $("#tooltip_PersonCnic").kendoTooltip({
        content: "Required. CNIC of Person involved",
        position: "top"
    });
    $("#tooltip_PersonRelation").kendoTooltip({
        content: "Required. Relationship type like s/o, d/o etc",
        position: "top"
    });
    $("#tooltip_PersonRelationName").kendoTooltip({
        content: "Required. Father Name / Husband Name / Wife Name etc",
        position: "top"
    });
    $("#tooltip_PersonPhone").kendoTooltip({
        content: "Required. Contact No. of Person involved",
        position: "top"
    });
    $("#tooltip_PersonEmail").kendoTooltip({
        content: "Email of Person involved",
        position: "top"
    });
    $("#tooltip_PersonAdress").kendoTooltip({
        content: "Required. Address of Person involved",
        position: "top"
    });
       
    $("#tooltip_volumeNumber").kendoTooltip({
        content: "Required. Volume Number",
        position: "top",
    });
    $("#tooltip_bookNumber").kendoTooltip({
        content: "Required. Book Number",
        position: "top",
    });
    $("#tooltip_deedNumber").kendoTooltip({
        content: "Required. Deed Number",
        position: "top",
    });

//$(".k-grid-toolbar", "#DenominationGrid").prepend("<h1>Denominations</h1>");

    $('#check').click(function checkForm(event) {
        $('#status').attr('class', 'inProgress');
        $('#status').text('Checking...');
        // get client-side Captcha object instance
        var captchaObj = $("#CaptchaCode").get(0).Captcha;
        // gather data required for Captcha validation
        var params = {}
        params.CaptchaId = captchaObj.Id;
        params.InstanceId = captchaObj.InstanceId;
        params.UserInput = $("#CaptchaCode").val();
        // make asynchronous Captcha validation request
        $.getJSON('../ChallanFormView/CheckCaptcha', params, function (result) {
            if (true === result) {
                //$('#status').attr('class', 'correct');
                //$('#status').text('');
            } else {
                //$('#status').attr('class', 'incorrect');
                //$('#status').text('Invalid Code Entered');
                $("#captchaError").show();
                document.getElementById("captchaError").innerHTML = InvalidCodeEntered;
                // always change Captcha code if validation fails
                captchaObj.ReloadImage();
                res = false;
            }
        });
        event.preventDefault();
    });
    queryStringName = getUrlVars()["name"];
    if (queryStringName == "GenerateChallan") {
        $('#GenerateNewChallan').prop('checked', true);
        if (queryStringName == "GenerateChallan") {
            createArrowBarFirstScreen();
        } else {
            removeArrowBarFirstScreen();
        }
    }

    $("#CaptchaCode").focus(function () {
        $("#captchaError").hide();
        document.getElementById("captchaError").innerHTML = "";
        var captchaObj = $("#CaptchaCode").get(0).Captcha;
        //captchaObj.ReloadImage();
    });

    //Call For Copying Fee Amount
    $.ajax({
        type: 'POST',
        url: base_url_service_layer + '/api/Proxy/CopyingFee/CopyingFeeAmount',
        contentType: "application/json;charset=utf-8",
        success: function (data) {
            console.log("" + data);
            copyingFeeAmount = data;
            $("#copyingFeeAmountText").val("" + returnCommas(copyingFeeAmount));
        },
        error: function (data) {
            var response = data.responseText.replace(/"/g, '');
            console.log(response);    
        }
    });


});
   
/////////////////////////////////////////////
//TextBoxes triggering event functions
////////////////////////////////////////////
function ResetDropDown(field) {

    $("#" + field).data("kendoDropDownList").value("");
    //$(".k-input").html("Select Relation");
}
/////////////////////////////////////////////
//Dropdowns triggering event functions
////////////////////////////////////////////

function populateTehsils() {
    $("#Tehsil").val("").data("kendoDropDownList").text(selectTehsilText);
    var id = $("#District").val();
    initializeDropDown(base_url_service_layer + "/api/Proxy/Locations/TehsilsByDistrictId?Id=" + id, selectTehsilText, "Tehsil");
}

function populateBranches() {
    $("#Branch").val("").data("kendoDropDownList").text(selectBranchText);
    var id = $("#District").val();
    initializeDropDown(base_url_service_layer + "/api/Proxy/Locations/BranchesforWhitePaper?Id=" + id, selectBranchText, "Branch");
}

function OnChangePurpose()
{
    var id = $("#Purpose").val();
    $.ajax({
        url: base_url_service_layer + '/api/Proxy/Locations/DenominationforWhitePaper?id=' + id,
        type: 'POST',
        contentType: "application/json;charset=utf-8",
        async: false,
        success: function (data) {
            //document.getElementById("Denomination").innerHTML = data;
            $('#Denomination').val(data.Name);
            DenominationId = data.Id;
        },
        error: function (data) {

        }
    });
}

function fillData() {
    $("#District").val("").data("kendoDropDownList").text("Attock");
    $("#Tehsil").val("").data("kendoDropDownList").text("Attock");
   

    $('#AgentName').val("Nadeem");
    $('#AgentCnic').val("34321-9983404-5");
    $('#AgentCell').val("0300-1234567");
    $('#agentEmail').val("nadeem@gmail.com");
    
   
    $("#PersonName").val("Nadeem");
    $("#PersonCnic").val("34321-9983404-5");
    $("#PersonEmail").val("nadeem@gmail.com");
    $("#PersonAddress").val("sdfsdf");
    $("#PersonPhone").val("0300-1234567");
    $("#RelationName").val("sdfsf");
    $("#Relation").val("").data("kendoDropDownList").text("D/O");


}

function renderChallanForWhitePaper(challan) {
    debugger
    var challanAmountView = challan.DenominationOfWhitePaper;
    document.getElementById("challanAmountText").innerHTML = returnCommas(challanAmountView);
    //document.getElementById("totalPayableAmountText2").innerHTML = returnCommas(challanAmountView);
    
    document.getElementById("districtText2").innerHTML = challan.DistrictString;
    document.getElementById("tehsilText2").innerHTML = challan.TehsilString;
    
    document.getElementById("purposetext").innerHTML = challan.PurposeString;
    document.getElementById("denominationtext").innerHTML = challan.DenominationOfWhitePaper;
   
    document.getElementById("reasontext").innerHTML = challan.ReasonOfWhitePaper;
    var $radio = $('input[name=InfoType]:checked');
    var id = $radio.attr('id');
    if (id == "Agent") {
        $('#AgentInfo').show();
        document.getElementById("agentNameText").innerHTML = challan.AgentName;
        document.getElementById("agentCninText").innerHTML = challan.AgentCnic;
        document.getElementById("agentContactText").innerHTML = challan.AgentCell;
        if (challan.AgentEmail == null || challan.AgentEmail == "") {
            $('#agentEmailLabel').hide();
            $('#agentEmailText').hide();
        }
        else {
            $('#agentEmailLabel').show();
            $('#agentEmailText').show();
            document.getElementById("agentEmailText").innerHTML = challan.AgentEmail;
        }
    } else {
        $('#AgentInfo').hide();
    }
    debugger
    $('#ApplicantDataGrid').kendoGrid({
        
        columns: [{ title: Name, field: "NameString", width: "250px" },
            { title: CNIC, field: "PersonCnic", width: "250px" },
            { field: "PersonPhoneMasked", title: 'Contact', width: "250px" },
            {
                command: [{
                    text: "",
                    name: "edit",
                    className: "edit-btn-center-adjustment",
                    imageClass: "fa fa-eye",
                    click: applicantDetails
                }
                ], width: "50px"
                , attributes: { class: "ob-center" }
            }
        ],

        dataSource: {
            data: challan.Party1
        },
        scrollable: false,
        sortable: false,
        filterable: false,
        pageable: false
    });
}

function applicantDetails(d) {
    debugger
    var grid = $('#data').data("kendoGrid");
    d.preventDefault();
    debugger;
    var dataItem = this.dataItem($(d.currentTarget).closest("tr"));
    document.getElementById("applicantNameText").innerHTML = dataItem.NameString;
    document.getElementById("applicantCNICText").innerHTML = dataItem.PersonCnic;
    document.getElementById("applicantContactText").innerHTML = dataItem.PersonPhoneMasked;
    document.getElementById("applicantAddressText").innerHTML = dataItem.PersonAddress;
    if (dataItem.PersonEmail == null || dataItem.PersonEmail == "") {
        $('#applicantEmail').hide();
    } else {
        $('#applicantEmail').show();

        //document.getElementById("").innerHTML = dataItem.PersonEmail;
        document.getElementById("applicantEmailText").innerHTML = dataItem.PersonEmail;
    }

    $("#applicantDetailedWindow").data("kendoWindow").title(applicantInfoLabel).center().open();
}


function createArrowBarFirstScreen() {

    $("#stepsArrowFirstScreen").show();
    $("#informationDiv").show();

    createArrow();
    var arrTotalStepImages = ['steps-pending', 'steps-pending'];
    var arrTotalStepImagesId = ['imageForFirstScreen', 'step4pending'];
    var arrTotalStepTitles = ['stepTitle-basicInfo', 'stepTitle-confirmation'];
    var arrTotalStepTitles_ur = ['stepTitle-basicInfo-ur','stepTitle-confirmation-ur'];
    if (document.cookie == "_culture=ur") {
        createArrowBarNew(arrTotalStepImages, arrTotalStepImagesId, arrTotalStepTitles_ur);
    }
    else {
        createArrowBarNew(arrTotalStepImages, arrTotalStepImagesId, arrTotalStepTitles);
    }
}

function createArrow()
{
    var count = $('#stepsArrowFirstScreen').find("#mainStepBar").size();
    if (count == 0) {
        var img = $('<img />', {
            src: '../Images/steps-bgArrow.png',
            id: 'mainStepBar',
            width: '1100px'
            //style: 'position: relative; left: -750px'
        });
        img.appendTo($('#stepsArrowFirstScreen'));
    }
}

function createStepTitle(imageName, imagePosition) {

    var img = $('<img />', {
        src: '../Images/' + imageName + '.png',
        style: 'position: absolute; left: ' + imagePosition + 'px;top:-55px;'
    });
    img.appendTo($('#informationDiv'));

}

function createStep(imageName, imageId, imagePosition) {
    var img = $('<img />', {
        src: '../Images/' + imageName + '.png',
        id: imageId,
        style: 'position: absolute;top: 13px;left:' + imagePosition + 'px;' //
    });
    img.appendTo($('#stepsArrow'));
}

function createArrowBarLast() {
    $("#stepsArrow").show();
    var arrTotalStepImages = [];//['steps-pending', 'steps-pending', 'steps-pending'];
    var arrTotalStepImagesId = [];
    var arrTotalStepTitles = [];//['stepTitle-basicInfo', 'stepTitle-DeedDetails', 'stepTitle-confirmation'];

    if (document.cookie == "_culture=ur") {
        arrTotalStepImages.push('steps-completed');
        arrTotalStepImagesId.push('imageForFirstScreen');
        arrTotalStepTitles.push('stepTitle-basicInfo-ur');
    }
    else {
        arrTotalStepImages.push('steps-completed');
        arrTotalStepImagesId.push('imageForFirstScreen');
        arrTotalStepTitles.push('stepTitle-basicInfo');

    }
    //if (document.cookie == "_culture=ur") {
    //    arrTotalStepImages.push('steps-completed');
    //    arrTotalStepImagesId.push('step1pending');
    //    arrTotalStepTitles.push('stepTitle-copyingFee-U');
    //}
    //else {
    //    arrTotalStepImages.push('steps-completed');
    //    arrTotalStepImagesId.push('step1pending');
    //    arrTotalStepTitles.push('stepTitle-copyingFee');
    //}
    if (document.cookie == "_culture=ur") {
        arrTotalStepImages.push('steps-completed');
        arrTotalStepImagesId.push('step4pending');
        arrTotalStepTitles.push('stepTitle-confirmation-ur');
    }
    else {
        arrTotalStepImages.push('steps-completed');
        arrTotalStepImagesId.push('step4pending');
        arrTotalStepTitles.push('stepTitle-confirmation');
    }

    createArrowBarNew(arrTotalStepImages, arrTotalStepImagesId, arrTotalStepTitles);


}

function createArrowBar() {
    $("#stepsArrow").show();
    var arrTotalStepImages = [];//['steps-pending', 'steps-pending', 'steps-pending'];
    var arrTotalStepImagesId = [];
    var arrTotalStepTitles = [];//['stepTitle-basicInfo', 'stepTitle-DeedDetails', 'stepTitle-confirmation'];

    if (document.cookie == "_culture=ur") {
        arrTotalStepImages.push('steps-completed');
        arrTotalStepImagesId.push('imageForFirstScreen');
        arrTotalStepTitles.push('stepTitle-basicInfo-ur');
    }
    else {
        arrTotalStepImages.push('steps-completed');
        arrTotalStepImagesId.push('imageForFirstScreen');
        arrTotalStepTitles.push('stepTitle-basicInfo');

    }
      
    //if (document.cookie == "_culture=ur") {
    //    // Exchange of Property case with CVT
    //    arrTotalStepImages.push('steps-pending');
    //    arrTotalStepImagesId.push('step1pending');
    //    arrTotalStepTitles.push('stepTitle-copyingFee-U');
    //}
    //else {
    //    // Exchange of Property case with CVT
    //    arrTotalStepImages.push('steps-pending');
    //    arrTotalStepImagesId.push('step1pending');
    //    arrTotalStepTitles.push('stepTitle-copyingFee');
    //}

    if (document.cookie == "_culture=ur") {
        arrTotalStepImages.push('steps-pending');
        arrTotalStepImagesId.push('step4pending');
        arrTotalStepTitles.push('stepTitle-confirmation-ur');
    }
    else {
        arrTotalStepImages.push('steps-pending');
        arrTotalStepImagesId.push('step4pending');
        arrTotalStepTitles.push('stepTitle-confirmation');
    }

    createArrowBarNew(arrTotalStepImages, arrTotalStepImagesId, arrTotalStepTitles);
           
        
}

function createArrowBarSecondScreen() {
    $("#stepsArrow").show();
    var arrTotalStepImages = [];//['steps-pending', 'steps-pending', 'steps-pending'];
    var arrTotalStepImagesId = [];
    var arrTotalStepTitles = [];//['stepTitle-basicInfo', 'stepTitle-DeedDetails', 'stepTitle-confirmation'];

    if (document.cookie == "_culture=ur") {
        arrTotalStepImages.push('steps-completed');
        arrTotalStepImagesId.push('imageForFirstScreen');
        arrTotalStepTitles.push('stepTitle-basicInfo-ur');
    }
    else {
        arrTotalStepImages.push('steps-completed');
        arrTotalStepImagesId.push('imageForFirstScreen');
        arrTotalStepTitles.push('stepTitle-basicInfo');

    }

    if (document.cookie == "_culture=ur") {
        arrTotalStepImages.push('steps-completed');
        arrTotalStepImagesId.push('step1pending');
        arrTotalStepTitles.push('stepTitle-copyingFee-U');
    }
    else {
        arrTotalStepImages.push('steps-completed');
        arrTotalStepImagesId.push('step1pending');
        arrTotalStepTitles.push('stepTitle-copyingFee');
    }

    if (document.cookie == "_culture=ur") {
        arrTotalStepImages.push('steps-pending');
        arrTotalStepImagesId.push('step4pending');
        arrTotalStepTitles.push('stepTitle-confirmation-ur');
    }
    else {
        arrTotalStepImages.push('steps-pending');
        arrTotalStepImagesId.push('step4pending');
        arrTotalStepTitles.push('stepTitle-confirmation');
    }

    createArrowBarNew(arrTotalStepImages, arrTotalStepImagesId, arrTotalStepTitles);


}


function createArrowBarNew(arrTotalStepImages, arrTotalStepImagesId, arrTotalStepTitles)
{
    var posFirstStep = 45;
    var posFirstStepTitle = 0;

    var posLastStep = 1000;
    var posLastStepTitle = 955;

    var len = -1;
    var isFirstAndSecondArrayLengthEqual = arrTotalStepImages.length == arrTotalStepImagesId.length;
    if (isFirstAndSecondArrayLengthEqual) len = arrTotalStepImages.length;

    if (len == arrTotalStepTitles.length)
    {
        var posStep = 0;
        var posStepTitle = 0;

        for (var i = 0 ; i < arrTotalStepTitles.length; i++)
        {
            if (i == 0)
            {
                // For First Step
                posStep = posFirstStep;
                posStepTitle = posFirstStepTitle;
            }
            else if (i == (arrTotalStepTitles.length - 1))
            {
                // For Last Step
                posStep = posLastStep;
                posStepTitle = posLastStepTitle;
            }
            //else
            //{
            //    // For intermediate steps
            //    var totalWidthBetweenFirstAndLastStepTitle = posLastStepTitle - posFirstStepTitle;
            //    var totalIntermediateSteps = arrTotalStepTitles.length - 2; // Remove first and last step

            //    var stepDistanceBetweenTwoTitle = totalWidthBetweenFirstAndLastStepTitle / (totalIntermediateSteps + 1);

            //    posStep += stepDistanceBetweenTwoTitle;
            //    posStepTitle += stepDistanceBetweenTwoTitle;
            //}
            createStepTitle(arrTotalStepTitles[i], posStepTitle);
            createStep(arrTotalStepImages[i], arrTotalStepImagesId[i], posStep);
        }
            
    }
}

function removeArrowBarFirstScreen() {
    //$("#stepsArrowFirstScreen img").remove();
    $("#informationDiv img").remove();
}
function removeArrowBarSecondScreen() {
    //$("#stepsArrowFirstScreen img").remove();
    $("#informationDiv img").remove();
}

function removeArrowBar() {
    //$("#stepsArrow img").remove();
    $("#informationDiv img").remove();
}
//-----------------------

function initializeDropDownWithText(url, placeholder, elementId, selectedText) {
    $("#" + elementId).kendoDropDownList({
        dataTextField: "Name",
        optionLabel: placeholder,
        dataValueField: "Id",
        //value: "", // used to select value from the dropdown on the basis of dataValueField
        dataSource: {
            transport: {
                read: {
                    url: url,
                    dataType: 'json',
                    type: 'POST',
                },
            },
        }, //datasource ending
        change: function (e) { // change event of drop down
            console.log("change event of dropdown");
        },
        dataBound: function (e) { // data bound event
            console.log("dataBound event of dropdown");
            // Select value of drop down 
            var dropdownlist = $("#" + elementId).data("kendoDropDownList");
            dropdownlist.select(function (dataItem) { // used to select value from the dropdown on the basis of dataTextField
                return dataItem.Name === selectedText;
            });
            dropdownlist.trigger("change");
        }
    });
    //debugger;
    //var dropdownlist = $("#" + elementId).data("kendoDropDownList");

    //dropdownlist.select(1);

    ////dropdownlist.select(function (dataItem) {
    ////    return dataItem.Name === selectedValue;
    ////});
    //dropdownlist.trigger("change");
}

function initializeDropDownWithId(url, placeholder, elementId, selectedId) {
    $("#" + elementId).kendoDropDownList({
        dataTextField: "Name",
        optionLabel: placeholder,
        dataValueField: "Id",
        //value: "", // used to select value from the dropdown on the basis of dataValueField
        dataSource: {
            transport: {
                read: {
                    url: url,
                    dataType: 'json',
                    type: 'POST',
                },
            },
        }, //datasource ending
        change: function (e) { // change event of drop down
            console.log("change event of prop area dropdown");
        },
        dataBound: function (e) { // data bound event
            console.log("dataBound event of prop area dropdown");
            // Select value of drop down 
            var dropdownlist = $("#" + elementId).data("kendoDropDownList");
            dropdownlist.value(selectedId);
            //dropdownlist.select(function (dataItem) { // used to select value from the dropdown on the basis of dataValueField
            //    dropdownlist.value(selectedId);
            //    return dataItem.value === selectedId;
            //});
            dropdownlist.trigger("change");
        }
    });
}

function initializeDropDownWithIdString(url, placeholder, elementId) {
    $("#" + elementId).kendoDropDownList({
        dataTextField: "Name",
        optionLabel: placeholder,
        dataValueField: "IdString",
        dataSource: {
            transport: {
                read: {
                    url: url,
                    dataType: 'json',
                    type: 'POST',
                },
            },

        },
    });
}
   
function urduToEnglish_AddChallan() {
    changeFloatingLabelOfElement("ChallanNo", "Enter Challan Number");
    changeFloatingLabelOfElement("StampNo", "Enter e-Stamp Number");
    $("#generateDefForOldStampAddChallan").html('<a href="../ChallanFormView/AddChallan?name=PayDeficiencyForOldRegistry&vCount=@HttpContext.Current.Application["NoOfVisitors"].ToString()&agree=true"> Generate Deficient Stamp/Penalty  </a><br /> <span style="color:grey;">(For old stamp papers)</span>');
    $("#generateDefCvtRegChallanLblAddChallan").html('<a href="../ChallanFormView/AddChallan?name=GenerateChallanForOldRegistry&vCount=@HttpContext.Current.Application["NoOfVisitors"].ToString()&agree=true">  Generate Deficient CVT/Registration Fee </a> <br/><span style="color:gray !important">(For old stamp papers)</span> ');
    $("#districtFloatingLbl").html('District');
    $("#tehsilFloatingLbl").html('Tehsil');
    $("#stampPaperTypeFloatingLbl").html('Stamp Paper Type');
    $("#deedNameFloatingLbl").html('Deed Name');
    $("#saleDeedNoteLbl").html(ForSaleDeedpleaseuseConveyance);
    $("#exemptStampDutyGiftDeedCheckbox").html(ExemptStampDutyegRuralagriculturallandforlegalheirsRegistrationCVTisapplicable);
    $("#isHousingSocietyInvolvedCheckbox").html('Is Housing Society Involved.');
    $("#purposeOfChallanLblAddChallan").html('Purpose of Challan');
    $("#challanAmountPaidByLblAddChallan").html('Challan Amount Paid By?');
    $("#districtFloatingLblReadOnly").html('District');
    $("#tehsilFloatingLblReadOnly").html('Tehsil');
    $("#stampPaperTypeFloatingLblReadOnly").html('Stamp Paper Type');
    $("#deedNameFloatingLblReadOnly").html('Deed Name');
    $("#purposeOfChallanTextDeficientLblAddChallan").html('Purpose of Challan');
    $("#oldRegNumFloatingLbl").html('Old Registry Number');
    $("#oldRegDateFloatingLbl").html('Old Registry Date');
    $("#agentNameFloatingLbl").html('Agent Name');
    $("#agentCnicFloatingLbl").html('Agent CNIC');
    $("#agentContactFloatingLbl").html('Agent Contact');
    $("#agentEmailFloatingLbl").html('Agent Email');
    urduToEnglish_PersonEdit();
    urduToEnglish_RateOfChallan();
            
}

function englishToUrdu_AddChallan() {
    changeFloatingLabelOfElement("ChallanNo", "چالان نمبر درج کریں");
    changeFloatingLabelOfElement("StampNo", "ای۔اسٹامپ نمبر درج کریں");
    $("#generateDefForOldStampAddChallan").html('<a href="../ChallanFormView/AddChallan?name=PayDeficiencyForOldRegistry&vCount=@HttpContext.Current.Application["NoOfVisitors"].ToString()&agree=true" style="font-family:MehrNastaliqWeb; font-size:120%;"> کم تخمینی کے بقا یا جات /  جرمانہ کی ادائیگی   </a><br /> <span style="color:grey; font-family:MehrNastaliqWeb; font-size:120%;">(پرانی رجسٹری کےلئے)</span>');
    $("#generateDefCvtRegChallanLblAddChallan").html('<a href="../ChallanFormView/AddChallan?name=GenerateChallanForOldRegistry&vCount=@HttpContext.Current.Application["NoOfVisitors"].ToString()&agree=true" style="font-family:MehrNastaliqWeb; font-size:120%;">  سی وی ٹی یا رجسٹریشن فیس اداکریں</a> <br/><span style="color:gray !important;font-family:MehrNastaliqWeb; font-size:120%;">(پرانی رجسٹری کےلئے)</span> ');
    $("#districtFloatingLbl").html('<span style="font-family:MehrNastaliqWeb; font-size:120%;">ضلع</span>');
    $("#tehsilFloatingLbl").html('<span style="font-family:MehrNastaliqWeb; font-size:120%;">تحصیل</span>');
    $("#stampPaperTypeFloatingLbl").html('<span style="font-family:MehrNastaliqWeb; font-size:120%;">اسٹامپ کی قسم</span>');
    $("#deedNameFloatingLbl").html('<span style="font-family:MehrNastaliqWeb; font-size:120%;">ڈیڈ نام</span>');
    $("#saleDeedNoteLbl").html(ForSaleDeedpleaseuseConveyance);
    $("#exemptStampDutyGiftDeedCheckbox").html('Exempt Stamp Duty (e.g. Rural agricultural land for legal heirs). Registration/CVT is applicable.');
    $("#isHousingSocietyInvolvedCheckbox").html('Is Housing Society Involved.');
    $("#purposeOfChallanLblAddChallan").html('<span style="font-family:MehrNastaliqWeb; font-size:120%;">چالان کا مقصد</span>');
    $("#challanAmountPaidByLblAddChallan").html('<span style="font-family:MehrNastaliqWeb; font-size:120%;">چالان رقم کی ادائیگی؟</span>');
    $("#districtFloatingLblReadOnly").html('<span style="font-family:MehrNastaliqWeb; font-size:120%;">ضلع</span>');
    $("#tehsilFloatingLblReadOnly").html('<span style="font-family:MehrNastaliqWeb; font-size:120%;">تحصیل</span>');
    $("#stampPaperTypeFloatingLblReadOnly").html('<span style="font-family:MehrNastaliqWeb; font-size:120%;">اسٹامپ کی قسم</span>');
    $("#deedNameFloatingLblReadOnly").html('<span style="font-family:MehrNastaliqWeb; font-size:120%;">ڈیڈکانام</span>');
    $("#purposeOfChallanTextDeficientLblAddChallan").html('<span style="font-family:MehrNastaliqWeb; font-size:120%;">چالان کا مقصد</span>');
    $("#oldRegNumFloatingLbl").html('<span style="font-family:MehrNastaliqWeb; font-size:120%;">پرانارجسٹری نمبر</span>');
    $("#oldRegDateFloatingLbl").html('<span style="font-family:MehrNastaliqWeb; font-size:120%;">پرانی رجسٹری تاریخ</span>');
    $("#agentNameFloatingLbl").html('<span style="font-family:MehrNastaliqWeb; font-size:120%;">ایجنٹ کا نام</span>');
    $("#agentCnicFloatingLbl").html('<span style="font-family:MehrNastaliqWeb; font-size:120%;">ایجنٹ کا قومی شناختی کارڈ نمبر</span>');
    $("#agentContactFloatingLbl").html('<span style="font-family:MehrNastaliqWeb; font-size:120%;">ایجنٹ کا رابطہ نمبر</span>');
    $("#agentEmailFloatingLbl").html('<span style="font-family:MehrNastaliqWeb; font-size:120%;">ایجنٹ کا ای میل</span>');
    englishToUrdu_PersonEdit();
    englishToUrdu_RateOfChallan();
}

function urduToEnglish_PersonEdit() {
    changeFloatingLabelOfElement("PersonName", "Name");
    changeFloatingLabelOfElement("PersonCnic", "CNIC");
    $("#relationDropDownLblPersonEdit").html('Relation');
    changeFloatingLabelOfElement("RelationName", "Relation Name");
    changeFloatingLabelOfElement("PersonPhone", "Contact Number");
    changeFloatingLabelOfElement("PersonEmail", "Email ID");
    changeFloatingLabelOfElement("PersonAddress", "Address");
}

function englishToUrdu_PersonEdit() {
    changeFloatingLabelOfElement("PersonName", "نام");
    changeFloatingLabelOfElement("PersonCnic", "قومی شناختی کارڈ نمبر");
    $("#relationDropDownLblPersonEdit").html('<span style="font-family:MehrNastaliqWeb; font-size:120%;">رشتہ</span>');
    changeFloatingLabelOfElement("RelationName", "رشتہ دار کا نام");
    changeFloatingLabelOfElement("PersonPhone", "رابطہ نمبر");
    changeFloatingLabelOfElement("PersonEmail", "ای میل");
    changeFloatingLabelOfElement("PersonAddress", "ایڈریس");
}

function changeLanguage() {
    //currentLanguage = $("#languageTranslateLink").text();
    //if (currentLanguage == "اردو") {
    //    englishToUrdu_AddChallan();
    //}
    //else {
    //    urduToEnglish_AddChallan();
    //}
}
function ResetPersonForm() {
    debugger
    ResetTextBox("PersonName");
    ResetTextBox("PersonEmail");
    ResetTextBox("PersonPhone");
    ResetTextBox("PersonAddress");
    ResetTextBox("PersonCnic");
    ResetTextBox("RelationName");
    ResetTextBox("PersonPhoneMasked");
    ResetDropDown("Relation");
    if (sessionStorage["currentLanguage"] == "Urdu") {
        $("#personForm > div:nth-child(2) > div:nth-child(2) > div > div > div > div").html("رشتہ");
    }
    else {
        $("#personForm > div:nth-child(2) > div:nth-child(2) > div > div > div > div").html("Relation");
    }
}
$("#CaptchaCode").keypress(function () {
    $("#captchaError").hide();
});

function onNextWhitePaper() {
    $("#ChallanErrorMessage").hide();
    debugger;
    if (isValid()) {
        //var captchaObj = $("#CaptchaCode").get(0).Captcha;
        if ($("#CaptchaCode").val() != null && $("#CaptchaCode").val() != "") {
            refreshCaptcha();
           //grecaptcha.reset(); 
           // captchaObj.ReloadImage();
            $("#captchaError").show();
            document.getElementById("captchaError").innerHTML = PleaseEnterAgain;
        }
        else {
            $("#challanform").hide();
            //$("#CopyingFeeAmount").hide();
            $("#confirmfrom").show();
            debugger
            challanModel = getChallanModel();
            renderChallanForWhitePaper(challanModel);

            removeArrowBarSecondScreen();
            createArrowBarSecondScreen();
        }
    } else {
        $("#ChallanErrorMessage").html(FormErrorMessage);
        $("#ChallanErrorMessage").css("color", "red");
        $("#ChallanErrorMessage").show();
    }
    
}
function OnNextFirstScreen() {
    debugger
    $("#ChallanErrorMessage").hide();

    if (isValid()) {
        //var captchaObj = $("#CaptchaCode").get(0).Captcha;
        if ($("#CaptchaCode").val() != null && $("#CaptchaCode").val() != "") {
            refreshCaptcha();
           //grecaptcha.reset(); 
            //captchaObj.ReloadImage();
            $("#captchaError").show();
            document.getElementById("captchaError").innerHTML = PleaseEnterAgain;
        }
    } else {
        $("#ChallanErrorMessage").html(FormErrorMessage);
        $("#ChallanErrorMessage").css("color", "red");
        $("#ChallanErrorMessage").show();
    }
}   
