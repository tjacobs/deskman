/*
 * SCSerial.h
 * hardware interface layer for waveshare serial bus servo
 * date: 2023.6.28 
 */

#ifndef _SCSERIAL_H
#define _SCSERIAL_H

#include "SCS.h"
#include <stdio.h>
#include <unistd.h>
#include "Serial.h"

class SCSerial : public SCS
{
public:
	SCSerial();
	SCSerial(u8 End);
	SCSerial(u8 End, u8 Level);

protected:
	virtual int writeSCS(unsigned char *nDat, int nLen);//output nLen byte
	virtual int readSCS(unsigned char *nDat, int nLen);//input nLen byte
	virtual int writeSCS(unsigned char bDat);//output 1 byte
	virtual void rFlushSCS();//
	virtual void wFlushSCS();//
public:
	unsigned long int IOTimeOut;//I/O timeout
	SerialPort *pSerial;//serial pointer
	int Err;
public:
	virtual int getErr(){  return Err;  }
};

#endif