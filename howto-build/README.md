# Assembling the OPSUM board (step-by-step visual guide)

![](../images/assembly/photo_2.jpg)

## Materials required

The following components are required (unless tagged "optional") to assemble the finished board.

Following is a BOM with relevant AliExpress links to source them:

| Item | Source / specification | Quantity to order | Optional |
| --- | --- | ---: | :---: |
| ESP32-S3-Zero MCU breakout board | [Link](https://aliexpress.com/item/1005009890203011.html), color: `S3-Zero` | 1 | No |
| 5V-5V 2W isolated DC/DC Converter | [Link](https://aliexpress.com/item/1005007625149966.html), color: `B0505S-2WR3` | 1 _(includes 5pcs)_ | No |
| Isolated 32A 4mm banana sockets | [Link](https://aliexpress.com/item/1005005352800055.html), color: `each color is 1 piece` | 1 _(includes 5pcs)_ | No |
| Female 6.3mm Faston connectors (50pcs) | [Link](https://aliexpress.com/item/1005009089161897.html), color: `F-Yellow-50PCS` | 1 _(includes 50pcs)_ | No |
| 12AWG wire 2m red/black | [Link](https://aliexpress.com/item/1005007468075329.html), color: `12AWG` | 1 _(includes 2 wires, 1m each)_ | No |
| M2.5 flat-head screws | 4 mm length | 8 | No |
| Acrylic rods | [Link](https://aliexpress.com/item/1005009389048602.html), `3 x 100 mm 10 pcs` | 1 | **Yes** |


## Before you start

Make sure you have the following tools:
- A decent soldering iron that can reach high enough temperature (~400°C)
- Solder (preferably leaded, for ease of use)
- Flux
- A crimper and a wire stripper (optional, but highly recommended)
- Isopropyl alcohol
- The OPSUM PCB and the above components
- _[OPTIONAL]_ a grinding pen or small Dremel-like rotary tool
- _[OPTIONAL]_ a PCB holder to aid with soldering

## Assembly steps

### 1. _[OPTIONAL]_ Cut the acrylic rods and fit them into the lid

The rods will be used as **led guides** for a better aesthetic look. Cut a single rod into **3** smaller rods with these specific sizes using your grinding pen:
- 2 x **20.5mm**
- 1 x **16.5mm**

Then push them into the enclosure lid holes (from the back side, as displayed in the picture) until they fit firmly. The two **longer** rods will be the _3V3_ and _5V_ led guides, and the **shorter** one will serve as the _Status_ led guide.

Optionally sand the ends of the cut rods to make them look smoother, using the rotary tool or a suitable tip of your grinding pen, or some fine grit sandpaper.

> Tip: when cutting the rods with your dremel/grinding pen, make sure you proceed at the **lowest possible speed**: acrylic melts easily with heat, and taking it slow ensures a cleaner, smoother and way more precise result (even without sanding). Try rotating the rod with your fingers while keeping the rotating blade firm and steady in your other hand.

![](../images/assembly/photo_3.jpg)

---

### 2. Install the banana sockets

Install the banana sockets into the enclosure base, and tighten them using the included metal rings.

**Make sure you install them one by one!**. Don't just fit them all first, you need to leave the necessary space for your fingers to tighten each ring.

![](../images/assembly/photo_5.jpg)
![](../images/assembly/photo_4.jpg)
![](../images/assembly/photo_6.jpg)

---

### 3. Solder the PCB pads and ESP32-S3-Zero breakout board

Solder the exposed PCB pads using **flux** and a **large solder tip**, with a high enough (~400-420°C) temperature to comfortably melt the solder and cover as much of the pads area as possible.

Then insert the pin headers in the ESP32 board as pictured below:

![](../images/assembly/photo_7.jpg)

And place the MCU breakout board in the OPSUM PCB. **Make sure the breakout board is in the correct orientation**:
![](../images/assembly/photo_8.jpg)


Then solder the each pin from the top side. Use **flux** and **a small soldering tip** with a temperature of ~300-320°C. If you have a PCB holder, use it to help keeping the PCB straight while you solder:

![](../images/assembly/photo_9.jpg)

This is what the breakout board should look like after soldering each pin:

![](../images/assembly/photo_11.jpg)

---

### 4. Install and solder the through hole components

Fit the breakout board whose pins you soldered before:

![](../images/assembly/photo_12.jpg)

And the **B0505S-2WR3 isolated DC/DC converter** in the correct orientation:

![](../images/assembly/photo_13.jpg)

Then apply **flux** and use a **small soldering tip** to solder them from below:

![](../images/assembly/photo_14.jpg)

This is what the board should look like once every **through-hole component** and **pad** is correctly soldered:

![](../images/assembly/photo_15.jpg)

---

### 5. Prepare the 12AWG wires

Cut the following pieces of 12AWG wire and use this table as a reference for soldering them to the pads later:

| Pad name | Color | Wire Length
| --- | --- | --- |
| PSU+ | RED | 10.5cm |
| PSU- | BLACK | 14cm |
| PROBE+ | RED | 10.5cm |
| DUT+ | RED | 7cm |
| DUT- | BLACK | 12cm |

---

### 6. Strip, crimp and solder the wires

First of all, strip each end of the wire, exposing ~1cm of copper on each side:

![](../images/assembly/photo_16.jpg)

Then crimp a female faston connector on **only one** side of the wire:

![](../images/assembly/photo_17.jpg)

And apply solder to the other end. Use **flux** and a **large soldering tip** to help with the job (12AWG wire is very thick, so it may take a moment to fully wet the copper):

![](../images/assembly/photo_18.jpg)

This is what the wire should look like in the end:

![](../images/assembly/photo_19.jpg)

Solder the wire to the appropriate PCB pad (use the reference above to figure out **pad, length and wire colors** to use):

![](../images/assembly/photo_20.jpg)

Eventually the board should look like this. Make sure the Probe+ wire doesn't touch the black wires above.

**Note that the black wires are shorted together, so they can be touching each other without problems**:

![](../images/assembly/photo_21.jpg)
![](../images/assembly/photo_22.jpg)
![](../images/assembly/photo_23.jpg)

---

### 7. Flash the firmware to the ESP32-S3 MCU

Connect a USB-C cable **from your PC to the MCU USBC PORT** _(Note: make sure you connect to the **MCU USB port, not the other (isolated) USB port**, otherwise your PC won't recognize the ESP32 board)_

Then flash the `firmware-s3-FULL.bin` on the board using `esptool`. 

> Detailed step-by-step instructions can be found [here](../dist#what-each-binary-is-for).

![](../images/assembly/photo_24.jpg)

---

### 8. Insert the PCB into the case and connect the sockets

Place the PCB in the enclosure base, and secure it using **four M2.5 screws**:

![](../images/assembly/photo_25.jpg)

Then connect each cable to its matching socket.

> Note: the sequence to follow is, from left to right:
>
> **| PSU+ | PSU- | PROBE+ | DUT+ | DUT- |**
> 
> You can also reference the socket names as printed on the enclosure lid.

![](../images/assembly/photo_26.jpg)

Finally, place the lid _(make sure the led guides fit between the wires, if you installed them)_ and secure it with **four M2.5 screws**:

![](../images/assembly/photo_27.jpg)

Connect the board's isolated USBC port to the PC, and connect to it using the included [GUI](../dist/opsum_gui.exe) or one of the [third party tools](../third_party_tools)

> Note: if your PC doesn't recognize the board as a COM port, make sure you have the correct CH340 drivers installed. These are the official driver download links depending on your OS:
>
> [Windows](https://www.wch-ic.com/downloads/CH341SER_EXE.html) - [Linux](https://www.wch-ic.com/downloads/CH341SER_LINUX_ZIP.html) - [MacOS](https://www.wch-ic.com/downloads/CH341SER_MAC_ZIP.html)

![](../images/assembly/photo_28.jpg)
